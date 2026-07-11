# -*- coding: utf-8 -*-
"""
Тест: та же боевая стратегия (XAUStrategy.evaluate(), те же сигналы входа),
но выход меняется — вместо ATR-based TP закрываем сделку сразу, как только
цена в первый раз даёт +100 (или +150) пунктов в плюс, до того как заденет
исходный SL. SL остаётся прежний (ATR-based, ~$6.80 в среднем по прошлым
тестам) — только TP укорачивается до фиксированных 100/150 пунктов.
"""
from __future__ import annotations

import argparse
import bisect
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import H1_COUNT, H4_COUNT, MAX_HOLD_BARS, detect_entry_tf_minutes, load_mt5_csv, resample
from strategy import StrategyParams, XAUStrategy

M15_COUNT = 50


def simulate_early_tp(setup, m15: list[dict], entry_idx: int, target_pts: float, pip: float) -> dict:
    is_long = setup.direction == "LONG"
    target = setup.entry + target_pts * pip if is_long else setup.entry - target_pts * pip
    for j in range(entry_idx + 1, min(entry_idx + 1 + MAX_HOLD_BARS, len(m15))):
        bar = m15[j]
        hit_sl = bar["low"] <= setup.sl if is_long else bar["high"] >= setup.sl
        hit_target = bar["high"] >= target if is_long else bar["low"] <= target
        if hit_sl:
            # консервативно: если в этом же баре задело и SL, и цель — считаем SL первым
            return {"result": "loss", "bars_held": j - entry_idx}
        if hit_target:
            return {"result": "win", "bars_held": j - entry_idx}
    return {"result": "timeout", "bars_held": MAX_HOLD_BARS}


def run(csv_path: str, target_pts: float, params: StrategyParams):
    print(f"Загружаю {csv_path}...")
    full_m15 = load_mt5_csv(csv_path)
    detected_tf = detect_entry_tf_minutes(full_m15)
    params.entry_tf_minutes = detected_tf
    print(f"Баров: {len(full_m15)} ({full_m15[0]['time']} — {full_m15[-1]['time']}), таймфрейм M{detected_tf}")

    full_h1 = resample(full_m15, 60)
    full_h4 = resample(full_m15, 240)
    h1_close_times = [c["time"] + timedelta(minutes=60) for c in full_h1]
    h4_close_times = [c["time"] + timedelta(minutes=240) for c in full_h4]

    strat = XAUStrategy(params)
    trades = []
    total = len(full_m15)
    for i in range(total):
        now = full_m15[i]["time"]
        h1_idx = bisect.bisect_right(h1_close_times, now)
        h4_idx = bisect.bisect_right(h4_close_times, now)
        h1_window = full_h1[max(0, h1_idx - H1_COUNT):h1_idx]
        h4_window = full_h4[max(0, h4_idx - H4_COUNT):h4_idx]
        m15_window = full_m15[max(0, i - M15_COUNT + 1):i + 1]

        setup = strat.evaluate(h4_window, h1_window, m15_window)
        if setup is not None:
            outcome = simulate_early_tp(setup, full_m15, i, target_pts, params.pip_size)
            sl_dist = abs(setup.entry - setup.sl)
            trades.append({"time": setup.time, "dir": setup.direction, "sl_dist": sl_dist, **outcome})

        if i % 20000 == 0 and i > 0:
            print(f"  {i}/{total}, сделок пока: {len(trades)}")

    decided = [t for t in trades if t["result"] in ("win", "loss")]
    wins = [t for t in decided if t["result"] == "win"]
    losses = [t for t in decided if t["result"] == "loss"]
    timeouts = [t for t in trades if t["result"] == "timeout"]
    win_rate = len(wins) / len(decided) * 100 if decided else 0
    span_days = max(1, (full_m15[-1]["time"] - full_m15[0]["time"]).days)

    print("\n" + "=" * 60)
    print(f"РАННИЙ ТЕЙК +{target_pts:.0f} пунктов — ОТЧЁТ")
    print("=" * 60)
    print(f"Всего сигналов: {len(trades)} | сделок/день: {len(trades)/span_days:.2f}")
    print(f"Win: {len(wins)}  Loss: {len(losses)}  Timeout: {len(timeouts)}")
    print(f"Win Rate: {win_rate:.1f}%")
    if losses:
        import statistics
        avg_sl = statistics.mean(t["sl_dist"] for t in losses)
        win_amt = target_pts * params.pip_size
        expectancy = (len(wins) * win_amt - len(losses) * avg_sl) / len(decided)
        print(f"Средний SL по убыточным: ${avg_sl:.2f}/унция")
        print(f"Тейк по выигрышным: ${win_amt:.2f}/унция (фикс.)")
        print(f"Экспектанси: {expectancy:+.3f}$/унция за сделку "
              f"(факт. R:R = {win_amt/avg_sl:.2f}, нужен винрейт для безубытка: {avg_sl/(avg_sl+win_amt)*100:.1f}%)")
    return trades


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--target-pts", type=float, default=125, help="Фиксированный ранний тейк в пунктах")
    args = ap.parse_args()
    run(args.csv, args.target_pts, StrategyParams())
