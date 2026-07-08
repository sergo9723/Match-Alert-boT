# -*- coding: utf-8 -*-
"""
Проверка конкретных наблюдений пользователя (месяц личной торговли на MT5,
время — Молдова, EET/EEST):
  1. 02:00-08:00 — "азиатская сессия, очень ликвидно"
  2. 03:00-04:30 — сигнальная свеча, ~70% продолжение движения 30 минут
  3. 09:00-14:00 — коррекция/боковик, открытие Лондона
  4. 14:00-17:00 — рост (LONG-перекос)
  5. Среда ~15:30 — американские новости, резкое движение (направление
     зависит от новости)
  6. 18:00-22:00 — снова активный рынок

ВАЖНО про часовой пояс: CSV из MT5 — время СЕРВЕРА БРОКЕРА, не Молдовы.
Из прошлого теста (FOMC): пик волатильности в день FOMC — час 21:00-22:00
сервера, а решение ФРС публикуют в 14:00 по Нью-Йорку => сервер = ET+7ч.
Молдова (EET/EEST) тоже = ET+7ч (обе стороны переходят на DST в одни и те
же недели по европейскому графику). Значит сервер MT5 ≈ время Молдовы, и
дополнительно подтверждаем это: часовой пояс EET совпадает и с тем, что
американские данные в 8:30 ET (ADP, розничные продажи и т.п., часто по
средам) лягут на 15:30 сервера/Молдовы — ровно то, что описал пользователь.
Поэтому дальше час CSV трактуем как час Молдовы напрямую, но само это
предположение тоже перепроверяем ниже (пункт 5).
"""
from __future__ import annotations

import argparse
import datetime
import os
import statistics
import sys
from collections import defaultdict

# Скрипт должен лежать в той же папке, что и backtest.py (python_bot/)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import load_mt5_csv

PIP = 0.01


def tstat(values):
    n = len(values)
    if n < 2:
        return 0.0
    m = statistics.mean(values)
    sd = statistics.stdev(values)
    if sd == 0:
        return 0.0
    return m / (sd / (n ** 0.5))


def window_stats(m15, hours):
    """Дневная агрегация по окну часов: open окна -> close окна, range, объём."""
    days = defaultdict(list)
    for b in m15:
        if b["time"].hour in hours:
            days[b["time"].date()].append(b)
    rows = []
    for d, bars in sorted(days.items()):
        bars.sort(key=lambda b: b["time"])
        o, c = bars[0]["open"], bars[-1]["close"]
        hi = max(b["high"] for b in bars)
        lo = min(b["low"] for b in bars)
        vol = sum(b["volume"] for b in bars)
        rows.append({
            "date": d, "move_pts": (c - o) / PIP, "range_pts": (hi - lo) / PIP,
            "volume": vol, "n": len(bars),
        })
    return rows


def print_window_summary(name, rows):
    moves = [r["move_pts"] for r in rows]
    ranges = [r["range_pts"] for r in rows]
    vols = [r["volume"] for r in rows]
    up = sum(1 for m in moves if m > 0)
    print(f"\n--- {name} ---")
    print(f"  дней={len(rows)}  avg_range={statistics.mean(ranges):.1f}pts  "
          f"avg_volume={statistics.mean(vols):.0f}  avg_move={statistics.mean(moves):+.1f}pts "
          f"(t={tstat(moves):+.2f})  %LONG-дней={up/len(moves)*100:.1f}%")


def continuation_test(m15):
    """Для каждого часа: направление 15-мин бара в этот час -> продолжается
    ли движение в ту же сторону ещё 30 минут (2 следующих бара)."""
    by_time = {b["time"]: b for b in m15}
    sorted_times = sorted(by_time.keys())
    idx_of = {t: i for i, t in enumerate(sorted_times)}

    hit = defaultdict(lambda: [0, 0])  # hour -> [hits, total]
    for i, t in enumerate(sorted_times):
        if i + 2 >= len(sorted_times):
            continue
        b = by_time[t]
        t1 = sorted_times[i + 1]
        t2 = sorted_times[i + 2]
        if t1 != t + datetime.timedelta(minutes=15) or t2 != t + datetime.timedelta(minutes=30):
            continue  # разрыв в данных (выходные и т.п.) — пропускаем
        sig_dir = b["close"] - b["open"]
        if sig_dir == 0:
            continue
        future_move = by_time[t2]["close"] - b["close"]
        if future_move == 0:
            continue
        same_sign = (sig_dir > 0) == (future_move > 0)
        h = t.hour
        hit[h][1] += 1
        if same_sign:
            hit[h][0] += 1
    return hit


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True, help="Путь к CSV с историей M15 (экспорт из MT5)")
    args = ap.parse_args()

    print("Загружаю", args.csv)
    m15 = load_mt5_csv(args.csv)
    print(f"Баров: {len(m15)}  {m15[0]['time']} -> {m15[-1]['time']}")
    mid = len(m15) // 2
    first_half, second_half = m15[:mid], m15[mid:]

    print("\n" + "=" * 78)
    print("1. 'АЗИЯ' 02:00-08:00 vs 'ЛОНДОН+' 14:00-17:00 vs 'ВЕЧЕР' 18:00-22:00")
    print("   vs 'КОРРЕКЦИЯ' 09:00-14:00  (весь диапазон часов включительно)")
    print("=" * 78)
    windows = {
        "02:00-08:00 (заявлено: 'очень ликвидно')": set(range(2, 9)),
        "09:00-14:00 (заявлено: 'боковик/коррекция')": set(range(9, 15)),
        "14:00-17:00 (заявлено: 'рост, LONG')": set(range(14, 18)),
        "18:00-22:00 (заявлено: 'снова активно')": set(range(18, 23)),
    }
    for name, hours in windows.items():
        rows = window_stats(m15, hours)
        print_window_summary(name, rows)

    print("\n  Для сравнения — САМОЕ ликвидное окно по факту (из прошлого анализа):")
    print_window_summary("15:00-18:00 (пик реального размаха/объёма)", window_stats(m15, {15, 16, 17, 18}))

    print("\n" + "=" * 78)
    print("2. ПРОДОЛЖЕНИЕ ДВИЖЕНИЯ НА 30 МИН ПОСЛЕ 15-МИН СВЕЧИ — ПО ВСЕМ ЧАСАМ")
    print("   (проверяем, действительно ли час 03:00 особенный, а не в целом рынок так работает)")
    print("=" * 78)
    hit_full = continuation_test(m15)
    hit_1 = continuation_test(first_half)
    hit_2 = continuation_test(second_half)
    print(f"{'час':>6} {'n':>6} {'hit%':>7}   {'H1 hit%':>9} {'H2 hit%':>9}  устойчиво?")
    for h in range(24):
        hits, total = hit_full.get(h, [0, 0])
        if total < 30:
            continue
        pct = hits / total * 100
        h1h, h1t = hit_1.get(h, [0, 0])
        h2h, h2t = hit_2.get(h, [0, 0])
        p1 = h1h / h1t * 100 if h1t else None
        p2 = h2h / h2t * 100 if h2t else None
        mark = ""
        if p1 is not None and p2 is not None and p1 > 55 and p2 > 55:
            mark = "  <== устойчиво >55% в обеих половинах"
        p1s = f"{p1:.1f}%" if p1 is not None else "  n/a"
        p2s = f"{p2:.1f}%" if p2 is not None else "  n/a"
        print(f"{h:02d}:00 {total:>6} {pct:>6.1f}%   {p1s:>9} {p2s:>9}  {mark}")

    print("\n  Точечно 03:00 (заявление: ~70% продолжение 30 мин):")
    hits, total = hit_full.get(3, [0, 0])
    print(f"  03:00 → факт: {hits}/{total} = {hits/total*100:.1f}%" if total else "  нет данных")

    print("\n" + "=" * 78)
    print("3. СРЕДА ~15:30 (гипотеза: 8:30 ET новости, напр. ADP) — час 15 сервера")
    print("=" * 78)
    wed_15 = []
    other_15 = []
    days_wed = defaultdict(list)
    days_other = defaultdict(list)
    for b in m15:
        if b["time"].hour != 15:
            continue
        if b["time"].weekday() == 2:  # среда
            days_wed[b["time"].date()].append(b)
        else:
            days_other[b["time"].date()].append(b)

    def day_range_move(days_dict):
        rows = []
        for d, bars in days_dict.items():
            bars.sort(key=lambda b: b["time"])
            o, c = bars[0]["open"], bars[-1]["close"]
            hi = max(b["high"] for b in bars)
            lo = min(b["low"] for b in bars)
            rows.append(((hi - lo) / PIP, (c - o) / PIP))
        return rows

    wed_rows = day_range_move(days_wed)
    other_rows = day_range_move(days_other)
    wed_ranges = [r[0] for r in wed_rows]
    other_ranges = [r[0] for r in other_rows]
    wed_moves = [r[1] for r in wed_rows]
    print(f"  Среда 15:00ч: n={len(wed_rows)}  avg_range={statistics.mean(wed_ranges):.1f}pts  "
          f"avg_move={statistics.mean(wed_moves):+.1f}pts (t={tstat(wed_moves):+.2f})")
    print(f"  Другие дни 15:00ч: n={len(other_rows)}  avg_range={statistics.mean(other_ranges):.1f}pts")
    print(f"  Отношение размаха среда/остальные: {statistics.mean(wed_ranges)/statistics.mean(other_ranges):.2f}x")


if __name__ == "__main__":
    main()
