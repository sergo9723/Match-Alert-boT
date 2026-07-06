# -*- coding: utf-8 -*-
"""
Полностью Python-бот сигналов по золоту — без TradingView и без вебхука.

Цикл: раз в POLL_SECONDS секунд тянем H4/H1/M15 свечи из выбранного
источника (yfinance — без регистрации, или OANDA demo — брокерские данные),
прогоняем через strategy.XAUStrategy (прямой порт Pine-логики), и если
появился новый сетап — просим Claude сверить его на согласованность
(ai_filter.ask_claude) и, если подтверждено, шлём в Telegram.

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
from strategy import StrategyParams, XAUStrategy
from telegram_notify import format_signal_message, send_telegram
from utils import log_signal

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("xau_signal_bot")

DATA_SOURCE = os.environ.get("DATA_SOURCE", "yfinance")
YFINANCE_TICKER = os.environ.get("YFINANCE_TICKER", "GC=F")
OANDA_API_KEY = os.environ.get("OANDA_API_KEY", "")
OANDA_INSTRUMENT = os.environ.get("OANDA_INSTRUMENT", "XAU_USD")
OANDA_ENVIRONMENT = os.environ.get("OANDA_ENVIRONMENT", "practice")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
NOTIFY_ON_REJECT = os.environ.get("NOTIFY_ON_REJECT", "false").lower() == "true"
SYMBOL_LABEL = os.environ.get("SYMBOL_LABEL", "XAUUSD")

POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))

H4_COUNT = 300
H1_COUNT = 1500
M15_COUNT = 50


def build_setup_dict(setup) -> dict:
    return {
        "symbol": SYMBOL_LABEL,
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


def main():
    if DATA_SOURCE == "oanda" and not OANDA_API_KEY:
        raise SystemExit("DATA_SOURCE=oanda, но OANDA_API_KEY не задан в .env")
    if not ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY не задан — Claude-фильтр будет отклонять все сигналы (fail-safe)")

    source = build_data_source(
        DATA_SOURCE,
        yfinance_ticker=YFINANCE_TICKER,
        oanda_api_key=OANDA_API_KEY,
        oanda_instrument=OANDA_INSTRUMENT,
        oanda_environment=OANDA_ENVIRONMENT,
    )
    strategy = XAUStrategy(StrategyParams())

    log.info(f"Старт: источник={DATA_SOURCE}, символ={SYMBOL_LABEL}, опрос каждые {POLL_SECONDS}с")

    while True:
        try:
            h4_df = source.get_candles("H4", H4_COUNT)
            h1_df = source.get_candles("H1", H1_COUNT)
            m15_df = source.get_candles("M15", M15_COUNT)

            log.info(f"Свечи: H4={len(h4_df)} H1={len(h1_df)} M15={len(m15_df)}"
                     + (f" | последняя M15: {m15_df['time'].iloc[-1]}" if not m15_df.empty else ""))

            setup = strategy.evaluate(h4_df, h1_df, m15_df)

            if setup is not None:
                setup_dict = build_setup_dict(setup)
                log.info(f"Сетап: {setup_dict}")

                confirmed, reason = ask_claude(setup_dict, ANTHROPIC_API_KEY)
                log_signal(setup_dict, confirmed, reason)

                if confirmed or NOTIFY_ON_REJECT:
                    send_telegram(
                        TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                        format_signal_message(setup_dict, confirmed, reason),
                    )
                log.info(f"Claude: {'CONFIRM' if confirmed else 'REJECT'} — {reason}")

        except Exception as e:
            log.error(f"Ошибка в цикле: {e}")

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
