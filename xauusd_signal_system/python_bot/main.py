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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from ai_filter import ask_claude
from data_sources import build_data_source
from instruments import parse_instruments_env
from strategy import StrategyParams, XAUStrategy
from telegram_notify import format_signal_message, send_telegram
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
    params = StrategyParams(pip_size=pip_size)
    if os.environ.get("REQUIRE_LIQUIDITY_SWEEP") is not None:
        params.require_liquidity_sweep = os.environ.get("REQUIRE_LIQUIDITY_SWEEP", "true").lower() == "true"
    if os.environ.get("MIN_BONUS") is not None:
        params.min_bonus = int(os.environ["MIN_BONUS"])
    return params


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
    for inst in instruments:
        sources[inst.label] = build_data_source(
            DATA_SOURCE,
            yfinance_ticker=inst.yahoo_ticker,
            oanda_api_key=OANDA_API_KEY,
            oanda_instrument=inst.oanda_instrument,
            oanda_environment=OANDA_ENVIRONMENT,
        )
        strategies[inst.label] = XAUStrategy(build_params(inst.pip_size))

    log.info(f"Старт: источник={DATA_SOURCE}, инструменты={[i.label for i in instruments]}, опрос каждые {POLL_SECONDS}с")

    while True:
        for label, source in sources.items():
            try:
                h4 = source.get_candles("H4", H4_COUNT)
                h1 = source.get_candles("H1", H1_COUNT)
                m15 = source.get_candles("M15", M15_COUNT)

                log.info(f"[{label}] Свечи: H4={len(h4)} H1={len(h1)} M15={len(m15)}"
                         + (f" | последняя M15: {m15[-1]['time']}" if m15 else ""))

                setup = strategies[label].evaluate(h4, h1, m15)

                if setup is not None:
                    setup_dict = build_setup_dict(label, setup)
                    log.info(f"[{label}] Сетап: {setup_dict}")

                    confirmed, reason = ask_claude(setup_dict, ANTHROPIC_API_KEY)
                    log_signal(setup_dict, confirmed, reason)

                    if confirmed or NOTIFY_ON_REJECT:
                        send_telegram(
                            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                            format_signal_message(setup_dict, confirmed, reason),
                        )
                    log.info(f"[{label}] Claude: {'CONFIRM' if confirmed else 'REJECT'} — {reason}")

            except Exception as e:
                log.error(f"[{label}] Ошибка в цикле: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
