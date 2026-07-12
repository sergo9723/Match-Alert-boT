# -*- coding: utf-8 -*-
"""
Тест: та же боевая стратегия (сигналы входа не меняются), но и TP, и SL —
фиксированные пункты от цены входа (не ATR-based). Считает P&L в долларах
при заданном лоте и балансе (0.01 лот = 1 унция золота, 1 пункт = $0.01
движения цены за унцию => 1 пункт на 0.1 лот = $0.01 * 10oz = $0.10).
"""
from __future__ import annotations

import argparse
import bisect
import os
import statistics
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest import H1_COUNT, H4_COUNT, MAX_HOLD_BARS, detect_entry_tf_minutes, load_mt5_csv, resample
from strategy import StrategyParams, XAUStrategy

M15_COUNT = 50
OZ_PER_STANDARD_LOT = 100  # 1.00 лот XAUUSD = 100 унций (стандартная спецификация)


def simulate_fixed(setup, m15: list[dict], entry_idx: int, tp_pts: float, sl_pts: float, pip: float) -> dict:
    is_long = setup.direction == "LONG"
    tp = setup.entry + tp_pts * pip if is_long else setup.entry - tp_pts * pip
    sl = setup.entry - sl_pts * pip if is_long else setup.entry + sl_pts * pip
    for j in range(entry_idx + 1, min(entry_idx + 1 + MAX_HOLD_BARS, len(m15))):
        bar = m15[j]
        hit_sl = bar["low"] <= sl if is_long else bar["high"] >= sl
        hit_tp = bar["high"] >= tp if is_long else bar["low"] <= tp
        if hit_sl:
            return {"result": "loss", "bars_held": j - entry_idx}
        if hit_tp:
            return {"result": "win", "bars_held": j - entry_idx}
    return {"result": "timeout", "bars_held": MAX_HOLD_BARS}


def run(csv_path: str, tp_pts: float, sl_pts: float, lot: float, balance: float, params: StrategyParams):
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
            outcome = simulate_fixed(setup, full_m15, i, tp_pts, sl_pts, params.pip_size)
            trades.append({"time": setup.time, "dir": setup.direction, **outcome})

        if i % 20000 == 0 and i > 0:
            print(f"  {i}/{total}, сделок пока: {len(trades)}")

    decided = [t for t in trades if t["result"] in ("win", "loss")]
    wins = [t for t in decided if t["result"] == "win"]
    losses = [t for t in decided if t["result"] == "loss"]
    timeouts = [t for t in trades if t["result"] == "timeout"]
    win_rate = len(wins) / len(decided) * 100 if decided else 0
    span_days = max(1, (full_m15[-1]["time"] - full_m15[0]["time"]).days)

    oz = lot * OZ_PER_STANDARD_LOT
    win_usd = tp_pts * params.pip_size * oz
    loss_usd = sl_pts * params.pip_size * oz
    total_pnl = len(wins) * win_usd - len(losses) * loss_usd
    breakeven_wr = loss_usd / (loss_usd + win_usd) * 100

    print("\n" + "=" * 60)
    print(f"ФИКС. TP={tp_pts:.0f}пт / SL={sl_pts:.0f}пт, лот={lot}, баланс=${balance:.0f} — ОТЧЁТ")
    print("=" * 60)
    print(f"Всего сигналов: {len(trades)} | сделок/день: {len(trades)/span_days:.2f}")
    print(f"Win: {len(wins)}  Loss: {len(losses)}  Timeout: {len(timeouts)}")
    print(f"Win Rate: {win_rate:.1f}%")
    print(f"\nПри лоте {lot} ({oz:.1f} унций):")
    print(f"  Прибыль за выигрышную сделку: +${win_usd:.2f}")
    print(f"  Убыток за проигрышную сделку: -${loss_usd:.2f} ({loss_usd/balance*100:.1f}% от баланса ${balance:.0f})")
    print(f"  Нужен винрейт для безубытка: {breakeven_wr:.1f}%")
    print(f"  Итоговый P&L за весь период: {total_pnl:+.2f}$ ({total_pnl/balance*100:+.1f}% от баланса)")
    if timeouts:
        print(f"  ⚠ {len(timeouts)} сделок не дошли ни до TP, ни до SL за {MAX_HOLD_BARS} баров (~{MAX_HOLD_BARS*15/60:.0f}ч) — не учтены в винрейте")
    return trades


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--tp-pts", type=float, required=True)
    ap.add_argument("--sl-pts", type=float, required=True)
    ap.add_argument("--lot", type=float, default=0.1)
    ap.add_argument("--balance", type=float, default=1000)
    args = ap.parse_args()
    run(args.csv, args.tp_pts, args.sl_pts, args.lot, args.balance, StrategyParams())
