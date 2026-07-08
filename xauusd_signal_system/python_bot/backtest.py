# -*- coding: utf-8 -*-
"""
Бэктест стратегии (strategy.py) на реальной истории XAUUSD — без numpy/pandas,
чтобы гарантированно работать на любом железе (см. main.py про SIGILL на
старых CPU).

Использует ТУ ЖЕ САМУЮ XAUStrategy.evaluate(), что и живой бот в main.py —
значит результат бэктеста реально отражает поведение бота, а не отдельную
переписанную "для теста" копию логики, которая могла бы незаметно разойтись.

Откуда взять данные:
    Экспорт истории из MetaTrader 5 (M15, XAUUSD, 3 года):
    1. В MT5: View -> History Center (или Инструменты -> Просмотр истории).
    2. Выберите XAUUSD, таймфрейм M15, скачайте максимум доступной истории
       (кнопка "Download"/"Загрузить" пока не упрётся в начало данных).
    3. Правой кнопкой по списку баров -> Export (Экспорт) -> сохраните .csv.
    Формат по умолчанию у MT5: <DATE> <TIME> <OPEN> <HIGH> <LOW> <CLOSE>
    <TICKVOL> <VOL> <SPREAD>, разделитель — таб или запятая. Этот скрипт
    сам определяет разделитель и пропускает строку заголовка.

Запуск:
    python3 backtest.py --csv XAUUSD_M15.csv
    python3 backtest.py --csv XAUUSD_M15.csv --start 2023-01-01 --end 2023-06-01  # для быстрой проверки на куске истории
"""
from __future__ import annotations

import argparse
import bisect
import csv as csv_module
import json
from datetime import datetime, timedelta

from strategy import StrategyParams, XAUStrategy

H4_COUNT = 300
H1_COUNT = 1500
M15_COUNT = 50
MAX_HOLD_BARS = 200  # ~50 часов на M15 — если за это время не пробило ни TP, ни SL, считаем "таймаут"


def load_mt5_csv(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        sample = f.readline()
        delimiter = "\t" if "\t" in sample else ("," if "," in sample else None)
        if delimiter is None:
            raise ValueError("Не смог определить разделитель CSV (ожидал таб или запятую)")
        f.seek(0)
        reader = csv_module.reader(f, delimiter=delimiter)

        rows = []
        for row in reader:
            if not row or len(row) < 6:
                continue
            first = row[0].strip().strip('"').strip("<").strip(">")
            if first.upper() in ("DATE", ""):
                continue  # строка заголовка
            try:
                date_str = row[0].strip().strip('"')
                time_str = row[1].strip().strip('"')
                dt = datetime.strptime(f"{date_str} {time_str}", "%Y.%m.%d %H:%M:%S")
            except ValueError:
                try:
                    dt = datetime.strptime(f"{date_str} {time_str}", "%Y.%m.%d %H:%M")
                except ValueError:
                    continue  # не смогли распарсить строку — пропускаем
            try:
                o, h, l, c = float(row[2]), float(row[3]), float(row[4]), float(row[5])
                vol = float(row[6]) if len(row) > 6 and row[6].strip() else 0.0
            except ValueError:
                continue
            rows.append({"time": dt, "open": o, "high": h, "low": l, "close": c, "volume": vol})

    rows.sort(key=lambda r: r["time"])
    return rows


def resample(candles: list[dict], minutes: int) -> list[dict]:
    buckets: dict[datetime, list[dict]] = {}
    for c in candles:
        total_min = c["time"].hour * 60 + c["time"].minute
        bucket_min = (total_min // minutes) * minutes
        day_start = c["time"].replace(hour=0, minute=0, second=0, microsecond=0)
        bucket_time = day_start + timedelta(minutes=bucket_min)
        buckets.setdefault(bucket_time, []).append(c)

    out = []
    for t in sorted(buckets.keys()):
        rows = buckets[t]
        out.append({
            "time": t,
            "open": rows[0]["open"],
            "high": max(r["high"] for r in rows),
            "low": min(r["low"] for r in rows),
            "close": rows[-1]["close"],
            "volume": sum(r["volume"] for r in rows),
        })
    return out


def simulate_outcome(setup, m15: list[dict], entry_idx: int) -> dict:
    """Идёт вперёд по M15-барам после сигнала и смотрит, что пробило раньше — TP или SL.
    Если в одном баре задело и то, и другое (гэп/крупная свеча) — консервативно
    считаем, что сначала сработал SL (стандартное допущение бэктестов)."""
    is_long = setup.direction == "LONG"
    for j in range(entry_idx + 1, min(entry_idx + 1 + MAX_HOLD_BARS, len(m15))):
        bar = m15[j]
        hit_sl = bar["low"] <= setup.sl if is_long else bar["high"] >= setup.sl
        hit_tp = bar["high"] >= setup.tp if is_long else bar["low"] <= setup.tp
        if hit_sl and hit_tp:
            return {"result": "loss", "bars_held": j - entry_idx}
        if hit_sl:
            return {"result": "loss", "bars_held": j - entry_idx}
        if hit_tp:
            return {"result": "win", "bars_held": j - entry_idx}
    return {"result": "timeout", "bars_held": MAX_HOLD_BARS}


def run_backtest(csv_path: str, start: datetime | None, end: datetime | None, params: StrategyParams | None = None) -> None:
    print(f"Загружаю {csv_path}...")
    full_m15 = load_mt5_csv(csv_path)
    if start:
        full_m15 = [c for c in full_m15 if c["time"] >= start]
    if end:
        full_m15 = [c for c in full_m15 if c["time"] <= end]
    if len(full_m15) < H1_COUNT * 4:
        raise SystemExit(f"Слишком мало данных ({len(full_m15)} M15-баров) — нужно минимум ~{H1_COUNT * 4} для прогрева EMA200")

    print(f"M15 баров: {len(full_m15)} ({full_m15[0]['time']} — {full_m15[-1]['time']})")
    print("Строю H1/H4 из M15...")
    full_h1 = resample(full_m15, 60)
    full_h4 = resample(full_m15, 240)
    h1_close_times = [c["time"] + timedelta(minutes=60) for c in full_h1]
    h4_close_times = [c["time"] + timedelta(minutes=240) for c in full_h4]

    strategy = XAUStrategy(params or StrategyParams())
    trades = []

    print("Прогоняю стратегию по истории (может занять несколько минут)...")
    total = len(full_m15)
    for i in range(total):
        now = full_m15[i]["time"]
        h1_idx = bisect.bisect_right(h1_close_times, now)
        h4_idx = bisect.bisect_right(h4_close_times, now)
        h1_window = full_h1[max(0, h1_idx - H1_COUNT):h1_idx]
        h4_window = full_h4[max(0, h4_idx - H4_COUNT):h4_idx]
        m15_window = full_m15[max(0, i - M15_COUNT + 1):i + 1]

        setup = strategy.evaluate(h4_window, h1_window, m15_window)
        if setup is not None:
            outcome = simulate_outcome(setup, full_m15, i)
            trades.append({
                "time": setup.time.isoformat(),
                "dir": setup.direction,
                "setup": setup.setup_type,
                "entry": round(setup.entry, 2),
                "sl": round(setup.sl, 2),
                "tp": round(setup.tp, 2),
                "bonus": setup.bonus,
                **outcome,
            })

        if i % 5000 == 0 and i > 0:
            print(f"  {i}/{total} баров обработано, сделок пока: {len(trades)}")

    print_report(trades, full_m15)
    write_trades_csv(trades, "backtest_trades.csv")
    return trades


def print_report(trades: list[dict], m15: list[dict]) -> None:
    decided = [t for t in trades if t["result"] in ("win", "loss")]
    wins = [t for t in decided if t["result"] == "win"]
    losses = [t for t in decided if t["result"] == "loss"]
    timeouts = [t for t in trades if t["result"] == "timeout"]

    span_days = max(1, (m15[-1]["time"] - m15[0]["time"]).days)
    win_rate = (len(wins) / len(decided) * 100) if decided else 0.0

    print("\n" + "=" * 60)
    print("ОТЧЁТ БЭКТЕСТА")
    print("=" * 60)
    print(f"Период:                 {m15[0]['time'].date()} — {m15[-1]['time'].date()} ({span_days} дней)")
    print(f"Всего сигналов:         {len(trades)}")
    print(f"  из них win:           {len(wins)}")
    print(f"  из них loss:          {len(losses)}")
    print(f"  из них timeout:       {len(timeouts)} (не дошли ни до TP, ни до SL за {MAX_HOLD_BARS} баров)")
    print(f"Win Rate (win/(win+loss)): {win_rate:.1f}%")
    print(f"Сделок в день (в среднем): {len(trades) / span_days:.2f}")

    by_dir: dict[str, list[dict]] = {}
    by_type: dict[str, list[dict]] = {}
    for t in decided:
        by_dir.setdefault(t["dir"], []).append(t)
        by_type.setdefault(t["setup"], []).append(t)

    print("\nПо направлению:")
    for d, ts in by_dir.items():
        w = sum(1 for t in ts if t["result"] == "win")
        print(f"  {d}: {len(ts)} сделок, win rate {w / len(ts) * 100:.1f}%")

    print("\nПо типу сетапа:")
    for st, ts in by_type.items():
        w = sum(1 for t in ts if t["result"] == "win")
        print(f"  {st}: {len(ts)} сделок, win rate {w / len(ts) * 100:.1f}%")

    print("\nПолный список сделок сохранён в backtest_trades.csv")
    print("=" * 60)


def write_trades_csv(trades: list[dict], path: str) -> None:
    if not trades:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv_module.DictWriter(f, fieldnames=list(trades[0].keys()))
        writer.writeheader()
        writer.writerows(trades)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Бэктест XAUUSD TA стратегии на истории из MT5")
    parser.add_argument("--csv", required=True, help="Путь к CSV с историей M15 (экспорт из MT5)")
    parser.add_argument("--start", help="Начало периода теста, YYYY-MM-DD (опционально)")
    parser.add_argument("--end", help="Конец периода теста, YYYY-MM-DD (опционально)")
    parser.add_argument("--require-liquidity-sweep", action="store_true",
                         help="Отключить слабый триггер 'Отбой от S/R', оставить только Liquidity Sweep")
    parser.add_argument("--rr", type=float, help="Переопределить R:R (по умолчанию 2.0)")
    parser.add_argument("--min-bonus", type=int, help="Переопределить мин. бонус-очков (по умолчанию 0)")
    parser.add_argument("--cooldown-bars", type=int, help="Переопределить кулдаун между сигналами, баров M15 (по умолчанию 6)")
    parser.add_argument("--sr-zone-pts", type=float, help="Переопределить зону реакции у уровня, pts (по умолчанию 300)")
    parser.add_argument("--pin-bar-ratio", type=float, help="Переопределить требуемое соотношение тень/тело для пинбара (по умолчанию 2.0)")
    parser.add_argument("--ema-period", type=int, help="Переопределить период EMA тренда (по умолчанию 200)")
    parser.add_argument("--ai-review", action="store_true",
                         help="Сразу после бэктеста отправить статистику на анализ Claude (нужен ANTHROPIC_API_KEY в .env)")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d") if args.start else None
    end_dt = datetime.strptime(args.end, "%Y-%m-%d") if args.end else None

    params = StrategyParams()
    if args.require_liquidity_sweep:
        params.require_liquidity_sweep = True
    if args.rr is not None:
        params.rr = args.rr
    if args.min_bonus is not None:
        params.min_bonus = args.min_bonus
    if args.cooldown_bars is not None:
        params.cooldown_bars = args.cooldown_bars
    if args.sr_zone_pts is not None:
        params.sr_zone_pts = args.sr_zone_pts
    if args.pin_bar_ratio is not None:
        params.pin_bar_ratio = args.pin_bar_ratio
    if args.ema_period is not None:
        params.ema_period = args.ema_period

    run_backtest(args.csv, start_dt, end_dt, params)

    if args.ai_review:
        from ai_review import ask_claude_review, load_backtest_csv

        print("\nЗапрашиваю анализ у Claude по итогам бэктеста...")
        stats = load_backtest_csv("backtest_trades.csv")
        review = ask_claude_review(stats, params)
        print("\n" + "=" * 60)
        print(review)
        print("=" * 60)
        from dataclasses import asdict
        with open("ai_review_report.md", "w", encoding="utf-8") as f:
            f.write("# ИИ-разбор бэктеста\n\n")
            f.write("## Статистика\n\n```json\n" + json.dumps(stats, ensure_ascii=False, indent=2) + "\n```\n\n")
            f.write("## Использованные настройки\n\n```json\n" + json.dumps(asdict(params), ensure_ascii=False, indent=2) + "\n```\n\n")
            f.write("## Анализ Claude\n\n" + review + "\n")
        print("\nОтчёт сохранён в ai_review_report.md")
