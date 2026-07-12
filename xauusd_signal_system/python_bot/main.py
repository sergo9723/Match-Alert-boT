# -*- coding: utf-8 -*-
"""
Полностью Python-бот сигналов — без TradingView и без вебхука, работает
параллельно на нескольких инструментах (диверсификация вместо ослабления
фильтра ради частоты сигналов — см. README).

Цикл: раз в POLL_SECONDS секунд тянем H4/H1/M15 свечи КАЖДОГО настроенного
инструмента (Yahoo Finance chart API — без регистрации, или OANDA demo —
брокерские данные), прогоняем через strategy.XAUStrategy (свой экземпляр
и своё состояние на каждый инструмент), и если появился новый сетап —
просим Claude сверить его на согласованность (ai_filter.ask_claude) и,
если подтверждено, шлём в Telegram с пометкой, по какому инструменту.

⚠️ Настройки фильтров (min_bonus, require_liquidity_sweep, pin_bar_ratio
и т.д.) откалиброваны бэктестом ТОЛЬКО для XAUUSD. Прежде чем доверять
сигналам по новым инструментам деньгами — прогоните backtest.py на их
собственной истории из MT5, как это делалось для золота (см. README).

Запуск:
    cp .env.example .env   # заполнить токены
    pip install -r ../requirements.txt
    python main.py
"""
import logging
import os
import time
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from ai_filter import ask_claude
from data_sources import build_data_source
from instruments import parse_instruments_env
from news_calendar import fomc_blackout
from risk_manager import RiskManager
from strategy import StrategyParams, XAUStrategy
from telegram_notify import format_signal_message, format_outcome_message, send_telegram
from utils import log_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("signal_bot")

DATA_SOURCE = os.environ.get("DATA_SOURCE", "yahoo")
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")
OANDA_ENVIRONMENT = os.environ.get("OANDA_ENVIRONMENT", "practice")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NOTIFY_ON_REJECT = os.environ.get("NOTIFY_ON_REJECT", "false").lower() == "true"

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))

H4_COUNT = 300
H1_COUNT = 1500
M15_COUNT = 50

# --- Риск-менеджмент (см. risk_manager.py) ---
ACCOUNT_BALANCE = float(os.environ.get("ACCOUNT_BALANCE", "1000"))
RISK_PCT = float(os.environ.get("RISK_PCT", "1.0"))
MAX_TRADES_PER_DAY = int(os.environ.get("MAX_TRADES_PER_DAY", "3"))
_weekly_limit_env = os.environ.get("WEEKLY_LOSS_LIMIT_R", "6")
WEEKLY_LOSS_LIMIT_R = None if _weekly_limit_env.strip().lower() == "none" else float(_weekly_limit_env)


def build_setup_dict(label: str, setup) -> dict:
    return {
        "symbol": label,
        "dir": setup.direction,
        "setup": setup.setup_type,
        "entry": round(setup.entry, 2),
        "sl": round(setup.sl, 2),
        "tp": round(setup.tp, 2),
        "trend": setup.trend,
        "volRatio": setup.vol_ratio,
        "volOK": setup.vol_ok,
        "pattern": setup.pattern_note,
        "bonus": setup.bonus,
    }


def build_params(pip_size: float) -> StrategyParams:
    # Лучшая найденная бэктестом связка на 3.5 годах XAUUSD (300 сделок,
    # +0.272R/сделку): RR=2.5, кулдаун 3 бара, блокировка входов в дни
    # FOMC — см. отчёт в README. Переопределяется через .env при желании
    # проверить другой вариант (сначала прогнать backtest.py).
    params = StrategyParams(pip_size=pip_size, rr=2.5, cooldown_bars=3, news_blackout=fomc_blackout())
    if os.environ.get("REQUIRE_LIQUIDITY_SWEEP") is not None:
        params.require_liquidity_sweep = os.environ.get("REQUIRE_LIQUIDITY_SWEEP", "true").lower() == "true"
    if os.environ.get("MIN_BONUS") is not None:
        params.min_bonus = int(os.environ["MIN_BONUS"])
    if os.environ.get("RR") is not None:
        params.rr = float(os.environ["RR"])
    if os.environ.get("COOLDOWN_BARS") is not None:
        params.cooldown_bars = int(os.environ["COOLDOWN_BARS"])
    return params


def check_open_signals(label: str, m15: list[dict], open_signals: list[dict],
                        risk: RiskManager, token: str, chat_id: str) -> list[dict]:
    """Проверяет ранее отправленные сигналы против новых свечей: если цена
    дошла до SL или TP — считает исход, кормит недельный риск-лимит и шлёт
    уведомление. Возвращает список сигналов, которые ещё не закрылись."""
    still_open = []
    for sig in open_signals:
        is_long = sig["direction"] == "LONG"
        result = None
        for bar in m15:
            if bar["time"] <= sig["time"]:
                continue
            hit_sl = bar["low"] <= sig["sl"] if is_long else bar["high"] >= sig["sl"]
            hit_tp = bar["high"] >= sig["tp"] if is_long else bar["low"] <= sig["tp"]
            if hit_sl:
                result = -1.0
                break
            if hit_tp:
                result = sig["rr"]
                break
        if result is None:
            still_open.append(sig)
            continue
        now = datetime.now(timezone.utc)
        week_total = risk.record_result(now, result)
        log.info(f"[{label}] Сделка {sig['direction']} от {sig['time']} закрылась: {result:+.1f}R "
                 f"(неделя пока: {week_total:+.1f}R)")
        send_telegram(token, chat_id, format_outcome_message(label, sig, result, week_total))
    return still_open


def main():
    if DATA_SOURCE == "oanda" and not OANDA_API_KEY:
        raise SystemExit("DATA_SOURCE=oanda, но OANDA_API_KEY не задан в .env")
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY не задан — Claude-фильтр будет отклонять все сигналы (fail-safe)")

    instruments = parse_instruments_env(os.environ.get("INSTRUMENTS", "XAUUSD"))
    if not instruments:
        raise SystemExit("Нет ни одного валидного инструмента в INSTRUMENTS")

    sources = {}
    strategies = {}
    open_signals: dict[str, list[dict]] = {}
    for inst in instruments:
        sources[inst.label] = build_data_source(
            DATA_SOURCE,
            yfinance_ticker=inst.yahoo_ticker,
            oanda_api_key=OANDA_API_KEY,
            oanda_instrument=inst.oanda_instrument,
            oanda_environment=OANDA_ENVIRONMENT,
        )
        strategies[inst.label] = XAUStrategy(build_params(inst.pip_size))
        open_signals[inst.label] = []

    risk = RiskManager(
        balance=ACCOUNT_BALANCE, risk_pct=RISK_PCT,
        max_trades_per_day=MAX_TRADES_PER_DAY, weekly_loss_limit_r=WEEKLY_LOSS_LIMIT_R,
    )
    log.info(f"Риск-менеджмент: баланс=${ACCOUNT_BALANCE:.0f}, риск={RISK_PCT}%/сделку, "
             f"макс.{MAX_TRADES_PER_DAY} сигнала/день, недельный лимит просадки="
             f"{'выкл' if WEEKLY_LOSS_LIMIT_R is None else f'-{WEEKLY_LOSS_LIMIT_R}R'}")
    log.info(f"Старт: источник={DATA_SOURCE}, инструменты={[i.label for i in instruments]}, опрос каждые {POLL_SECONDS}с")

    while True:
        for label, source in sources.items():
            try:
                h4 = source.get_candles("H4", H4_COUNT)
                h1 = source.get_candles("H1", H1_COUNT)
                m15 = source.get_candles("M15", M15_COUNT)

                log.info(f"[{label}] Свечи: H4={len(h4)} H1={len(h1)} M15={len(m15)}"
                         + (f" | последняя M15: {m15[-1]['time']}" if m15 else ""))

                open_signals[label] = check_open_signals(
                    label, m15, open_signals[label], risk, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                )

                setup = strategies[label].evaluate(h4, h1, m15)

                if setup is not None:
                    now = m15[-1]["time"]
                    can_open, block_reason = risk.can_open(now)
                    if not can_open:
                        log.info(f"[{label}] Сетап есть, но пропускаю: {block_reason}")
                        continue

                    lot = risk.lot_size(setup.entry, setup.sl, strategies[label].p.pip_size)
                    if lot <= 0:
                        log.warning(f"[{label}] Пропускаю сетап: при балансе ${ACCOUNT_BALANCE:.0f} и риске "
                                    f"{RISK_PCT}% минимальный лот ({risk.min_lot}) уже превышает целевой риск "
                                    f"на этом SL — депозит мал для этой сделки")
                        continue
                    setup_dict = build_setup_dict(label, setup)
                    setup_dict["lot"] = lot
                    log.info(f"[{label}] Сетап: {setup_dict}")

                    confirmed, reason = ask_claude(setup_dict, ANTHROPIC_API_KEY)
                    log_signal(setup_dict, confirmed, reason)

                    if confirmed or NOTIFY_ON_REJECT:
                        send_telegram(
                            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                            format_signal_message(setup_dict, confirmed, reason),
                        )
                    log.info(f"[{label}] Claude: {'CONFIRM' if confirmed else 'REJECT'} — {reason}")

                    if confirmed:
                        risk.record_signal(now)
                        open_signals[label].append({
                            "direction": setup.direction, "sl": setup.sl, "tp": setup.tp,
                            "time": setup.time, "rr": strategies[label].p.rr,
                        })

            except Exception as e:
                log.error(f"[{label}] Ошибка в цикле: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
