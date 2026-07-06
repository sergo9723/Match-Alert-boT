# -*- coding: utf-8 -*-
"""
Webhook-мост: TradingView alert -> Claude (доп.фильтр) -> Telegram.

Pine-скрипт (XAUUSD_TA_Master_v6.pine) сам считает весь технический анализ
(тренд, S/R, пробой+ретест, объём, свечные паттерны, графические фигуры) и
при сигнале шлёт alert() с JSON-описанием сетапа на URL этого сервиса.

Этот сервис НЕ делает собственный технический анализ и НЕ видит график —
он только прогоняет уже готовый сетап через Claude как логическую
проверку согласованности (аномалии/противоречия в данных), и если Claude
подтверждает — пересылает сигнал в Telegram.
"""
import json
import logging
import os
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "REPLACE_ME")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")
NOTIFY_ON_REJECT = os.environ.get("NOTIFY_ON_REJECT", "false").lower() == "true"
LOG_FILE = os.environ.get("SIGNAL_LOG_FILE", "signals_log.jsonl")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s")
log = logging.getLogger("webhook_relay")

app = Flask(__name__)

_anthropic_client = None


def get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        from anthropic import Anthropic
        _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    return _anthropic_client


def ask_claude(setup: dict) -> tuple[bool, str]:
    """
    Просит Claude проверить согласованность сетапа перед отправкой в Telegram.
    Claude НЕ видит график — только структурированные поля из Pine.
    Возвращает (confirmed, reason).
    """
    prompt = f"""Ты — риск-осторожный ассистент трейдера. Тебе присылают уже готовый
торговый сетап по золоту (XAUUSD), собранный механическими правилами технического
анализа (тренд + уровень S/R + пробой/ретест или отбой + свечное подтверждение).
Ты НЕ видишь график — только эти поля:

Направление: {setup.get('dir')}
Тип сетапа: {setup.get('setup')}
Тренд: {setup.get('trend')}
Вход: {setup.get('entry')}
SL: {setup.get('sl')}
TP: {setup.get('tp')}
Объём подтверждён: {setup.get('volOK')} (соотношение {setup.get('volRatio')})
Графическая фигура рядом: {setup.get('pattern')}
Бонус-очки (объём+фигура): {setup.get('bonus')}

Проверь только логическую согласованность (не гадай о будущей цене):
- Соответствует ли SL/TP направлению сделки (SL и TP по разные стороны от входа,
  в правильную сторону для {setup.get('dir')})?
- Нет ли явного противоречия (например "⚠ Дв.вершина" при LONG, "⚠ Дв.дно" при SHORT)?
- Разумное ли расстояние SL/TP (не подозрительно близко/далеко)?

Ответь СТРОГО в формате двух строк:
CONFIRM или REJECT
Одна короткая причина (на русском, не длиннее 20 слов)"""

    try:
        client = get_anthropic_client()
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        verdict = lines[0].upper() if lines else "REJECT"
        reason = lines[1] if len(lines) > 1 else "нет причины"
        confirmed = verdict.startswith("CONFIRM")
        return confirmed, reason
    except Exception as e:
        log.error(f"Claude API error: {e}")
        # Fail-safe: если ИИ недоступен, сигнал НЕ подтверждаем автоматически —
        # лучше пропустить сделку, чем послать неотфильтрованный сигнал молча.
        return False, f"Ошибка Claude API: {e}"


def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram не настроен (нет TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) — пропускаю отправку")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }, timeout=10)
        if not r.ok:
            log.error(f"Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


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


def log_signal(setup: dict, confirmed: bool, reason: str):
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "setup": setup,
        "ai_confirmed": confirmed,
        "ai_reason": reason,
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error(f"Не смог записать в журнал сигналов: {e}")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/webhook", methods=["POST"])
def webhook():
    raw = request.get_data(as_text=True)
    try:
        setup = json.loads(raw)
    except json.JSONDecodeError:
        log.error(f"Не JSON в теле запроса: {raw[:200]}")
        return jsonify({"error": "invalid json"}), 400

    if setup.get("secret") != WEBHOOK_SECRET:
        log.warning("Неверный секрет вебхука — запрос отклонён")
        return jsonify({"error": "forbidden"}), 403

    log.info(f"Получен сетап: {setup.get('symbol')} {setup.get('dir')} {setup.get('setup')}")

    confirmed, reason = ask_claude(setup)
    log_signal(setup, confirmed, reason)

    if confirmed or NOTIFY_ON_REJECT:
        send_telegram(format_signal_message(setup, confirmed, reason))

    return jsonify({"confirmed": confirmed, "reason": reason})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
