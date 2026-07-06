# -*- coding: utf-8 -*-
import logging

import requests

log = logging.getLogger("telegram_notify")


def send_telegram(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        log.warning("Telegram не настроен (нет токена/chat_id) — пропускаю отправку")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)
        if not r.ok:
            log.error(f"Telegram error {r.status_code}: {r.text}")
        return r.ok
    except Exception as e:
        log.error(f"Telegram send failed: {e}")
        return False


def format_signal_message(setup: dict, confirmed: bool, reason: str) -> str:
    arrow = "📈" if setup.get("dir") == "LONG" else "📉"
    status = "✅ ПОДТВЕРЖДЕНО ИИ" if confirmed else "⛔ ОТКЛОНЕНО ИИ"
    return (
        f"{arrow} *{setup.get('symbol')} {setup.get('dir')}* — {setup.get('setup')}\n"
        f"Вход: `{setup.get('entry')}`\n"
        f"TP: `{setup.get('tp')}`  |  SL: `{setup.get('sl')}`\n"
        f"Тренд: {setup.get('trend')}  |  Объём: {setup.get('volRatio')}x\n"
        f"Фигура: {setup.get('pattern')}\n"
        f"{status}: _{reason}_"
    )
