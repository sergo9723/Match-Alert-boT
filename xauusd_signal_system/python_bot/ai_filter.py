# -*- coding: utf-8 -*-
"""
Claude как доп.фильтр перед отправкой сигнала. Не видит график — только
структурированные поля сетапа, посчитанные strategy.py. Проверяет
логическую согласованность (SL/TP по правильную сторону, нет явного
противоречия с графической фигурой), не пытается предсказать цену.
"""
import logging
import os

log = logging.getLogger("ai_filter")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")

_client = None


def _get_client(api_key: str):
    global _client
    if _client is None:
        from anthropic import Anthropic
        _client = Anthropic(api_key=api_key)
    return _client


def ask_claude(setup: dict, api_key: str) -> tuple[bool, str]:
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
        client = _get_client(api_key)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        verdict = lines[0].upper() if lines else "REJECT"
        reason = lines[1] if len(lines) > 1 else "нет причины"
        return verdict.startswith("CONFIRM"), reason
    except Exception as e:
        log.error(f"Claude API error: {e}")
        # Fail-safe: если ИИ недоступен — сигнал не подтверждаем автоматически,
        # лучше пропустить сделку, чем послать неотфильтрованный сигнал молча.
        return False, f"Ошибка Claude API: {e}"
