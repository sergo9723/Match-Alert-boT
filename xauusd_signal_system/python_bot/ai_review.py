# -*- coding: utf-8 -*-
"""
ИИ-разбор статистики сделок через Claude — "разумное" применение ИИ к
стратегии: не "самообучение", а анализ уже посчитанных цифр (из бэктеста
или из живого журнала бота) и предложения, что подкрутить в StrategyParams.
Решение всегда принимает человек — скрипт только пишет отчёт, ничего не
меняет в коде сам.

Запуск:
    python3 ai_review.py --source backtest_trades.csv
    python3 ai_review.py --source signals_log.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5")


def load_backtest_csv(path: str) -> dict:
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    decided = [r for r in rows if r["result"] in ("win", "loss")]
    wins = [r for r in decided if r["result"] == "win"]
    by_type: dict[str, dict] = {}
    for r in decided:
        st = by_type.setdefault(r["setup"], {"total": 0, "wins": 0})
        st["total"] += 1
        if r["result"] == "win":
            st["wins"] += 1
    return {
        "source": "backtest",
        "total_signals": len(rows),
        "decided": len(decided),
        "wins": len(wins),
        "win_rate_pct": round(len(wins) / len(decided) * 100, 1) if decided else 0,
        "timeouts": sum(1 for r in rows if r["result"] == "timeout"),
        "by_setup_type": {
            k: {"total": v["total"], "win_rate_pct": round(v["wins"] / v["total"] * 100, 1)}
            for k, v in by_type.items()
        },
    }


def load_live_log(path: str) -> dict:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    confirmed = [r for r in records if r.get("ai_confirmed")]
    rejected = [r for r in records if not r.get("ai_confirmed")]
    by_type: dict[str, int] = {}
    for r in records:
        st = r.get("setup", {}).get("setup", "?")
        by_type[st] = by_type.get(st, 0) + 1
    return {
        "source": "live_log",
        "total_signals": len(records),
        "confirmed_by_claude": len(confirmed),
        "rejected_by_claude": len(rejected),
        "by_setup_type": by_type,
        "note": "Живой журнал не знает исходов сделок (TP/SL) — только какие сигналы были и подтвердил ли их ИИ-фильтр. Для реального винрейта используйте backtest.py.",
    }


def ask_claude_review(stats: dict, params=None) -> str:
    params_section = ""
    if params is not None:
        from dataclasses import asdict
        params_section = (
            "\nФактические настройки StrategyParams, использованные в этом прогоне "
            "(это ПОЛНЫЙ список полей класса — других параметров в коде нет, "
            "используй в предложениях ТОЛЬКО эти имена, ничего не придумывай):\n"
            + json.dumps(asdict(params), ensure_ascii=False, indent=2) + "\n"
        )

    prompt = f"""Ты анализируешь статистику работы механической торговой системы по золоту
(XAUUSD), собранной из фильтров технического анализа (тренд, S/R, пробой+ретест,
liquidity sweep, свечные паттерны, FVG, CHoCH). Вот статистика:

{json.dumps(stats, ensure_ascii=False, indent=2)}
{params_section}
Дай краткий анализ (на русском, не длиннее 300 слов):
1. Что говорят цифры о качестве системы — честно, без приукрашивания. Используй
   фактический R:R (поле rr выше) для расчёта точки безубыточности, не гадай.
2. Если видно, что какой-то тип сетапа (setup_type) заметно слабее других —
   укажи это конкретно. Если тип всего один — так и скажи, не выдумывай проблему.
3. Предложи 2-3 КОНКРЕТНЫХ параметра — СТРОГО из списка полей StrategyParams
   выше (если список не передан — прямо напиши, что не можешь дать точные
   имена без него), и в какую сторону их менять, с обоснованием.
4. НЕ обещай рост винрейта до конкретных больших чисел — только направление
   изменений и что стоит перепроверить бэктестом после правки."""

    from anthropic import Anthropic
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=2000,  # русский текст токенизируется плотнее английского — 800 обрезало ответ на полуслове
        messages=[{"role": "user", "content": prompt}],
    )
    text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
    return "\n".join(text_blocks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ИИ-разбор статистики сделок (бэктест или живой журнал)")
    parser.add_argument("--source", required=True, help="backtest_trades.csv или signals_log.jsonl")
    args = parser.parse_args()

    if not ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY не задан в .env")

    if args.source.endswith(".jsonl"):
        stats = load_live_log(args.source)
    else:
        stats = load_backtest_csv(args.source)

    print("Статистика:")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    print("\nЗапрашиваю анализ у Claude...")
    review = ask_claude_review(stats)

    print("\n" + "=" * 60)
    print(review)
    print("=" * 60)

    with open("ai_review_report.md", "w", encoding="utf-8") as f:
        f.write(f"# ИИ-разбор ({stats['source']})\n\n")
        f.write("## Статистика\n\n```json\n" + json.dumps(stats, ensure_ascii=False, indent=2) + "\n```\n\n")
        f.write("## Анализ Claude\n\n" + review + "\n")
    print("\nОтчёт сохранён в ai_review_report.md")
