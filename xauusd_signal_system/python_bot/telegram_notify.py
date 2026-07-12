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
    lot = setup.get("lot")
    lot_line = f"Лот (риск-менеджер): `{lot}`\n" if lot else ""
    return (
        f"{arrow} *{setup.get('symbol')} {setup.get('dir')}* — {setup.get('setup')}\n"
        f"Вход: `{setup.get('entry')}`\n"
        f"TP: `{setup.get('tp')}`  |  SL: `{setup.get('sl')}`\n"
        f"{lot_line}"
        f"Тренд: {setup.get('trend')}  |  Объём: {setup.get('volRatio')}x\n"
        f"Фигура: {setup.get('pattern')}\n"
        f"{status}: _{reason}_"
    )


def format_outcome_message(label: str, sig: dict, r_multiple: float, week_total_r: float) -> str:
    won = r_multiple > 0
    icon = "✅" if won else "❌"
    label_word = "TP (профит)" if won else "SL (убыток)"
    return (
        f"{icon} *{label} {sig['direction']}* от {sig['time'].strftime('%Y-%m-%d %H:%M')} — {label_word}\n"
        f"Результат: `{r_multiple:+.1f}R`\n"
        f"Накоплено за эту неделю: `{week_total_r:+.1f}R`"
    )
