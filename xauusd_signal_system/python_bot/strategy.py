# -*- coding: utf-8 -*-
"""
Прямой порт логики XAUUSD_TA_Master_v6.pine на Python (pandas), чтобы
сигналы можно было генерировать без TradingView.

Один сетап = тренд (Блок 3/7) + триггер (Блок 5: пробой+ретест ИЛИ отбой от
уровня по тренду, Блок 4) + свечное подтверждение (Блок 8), без встречной
графической фигуры (Блок 9). Объём (Блок 6) — бонус к уверенности.

Состояние (ожидание ретеста после пробоя, антиспам-кулдаун) хранится в
атрибутах XAUStrategy и переживает между вызовами evaluate() — как `var`
в Pine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd


@dataclass
class StrategyParams:
    ema_period: int = 200
    flat_atr_mult: float = 0.6

    sr_pivot_lr: int = 3
    sr_max_levels: int = 15
    sr_zone_pts: float = 300

    breakout_buf_pts: float = 150
    retest_min_bars: int = 2
    retest_max_bars: int = 20

    vol_period: int = 20
    vol_mult: float = 1.3

    pin_bar_ratio: float = 2.0
    doji_max_body: float = 0.1

    pattern_tol_pts: float = 250

    use_atr: bool = True
    atr_period: int = 14
    atr_sl_mult: float = 1.5
    rr: float = 2.0
    sl_pts: float = 500
    tp_pts: float = 1000

    min_bonus: int = 0
    cooldown_bars: int = 6

    pip_size: float = 0.01  # syminfo.mintick аналог: 1 pt для XAUUSD = 0.01


@dataclass
class Setup:
    direction: str          # "LONG" | "SHORT"
    setup_type: str         # "Пробой+Ретест" | "Отбой от S/R"
    entry: float
    sl: float
    tp: float
    trend: str
    vol_ratio: float
    vol_ok: bool
    pattern_note: str
    bonus: int
    time: datetime


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def find_pivots(df: pd.DataFrame, left: int, right: int) -> tuple[list[int], list[int]]:
    """Индексы подтверждённых pivot high / pivot low (симметрично left/right)."""
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)
    piv_high, piv_low = [], []
    for i in range(left, n - right):
        window_h = highs[i - left:i + right + 1]
        if highs[i] == window_h.max() and (window_h == highs[i]).sum() == 1:
            piv_high.append(i)
        window_l = lows[i - left:i + right + 1]
        if lows[i] == window_l.min() and (window_l == lows[i]).sum() == 1:
            piv_low.append(i)
    return piv_high, piv_low


def nearest_above(levels: list[float], price: float) -> Optional[float]:
    candidates = [lv for lv in levels if lv > price]
    return min(candidates) if candidates else None


def nearest_below(levels: list[float], price: float) -> Optional[float]:
    candidates = [lv for lv in levels if lv < price]
    return max(candidates) if candidates else None


class XAUStrategy:
    def __init__(self, params: StrategyParams | None = None):
        self.p = params or StrategyParams()

        # состояние пробоя+ретеста (аналог var в Pine)
        self.pending_dir = 0          # 1 = пробили сопротивление вверх, -1 = пробили поддержку вниз
        self.pending_level: Optional[float] = None
        self.pending_breakout_time: Optional[datetime] = None

        # антиспам
        self.last_long_time: Optional[datetime] = None
        self.last_short_time: Optional[datetime] = None

        # чтобы не обрабатывать один и тот же закрытый M15-бар дважды
        self.last_processed_m15_time: Optional[datetime] = None

    def _cooldown_ok(self, last_time: Optional[datetime], now: datetime) -> bool:
        if last_time is None:
            return True
        return now - last_time >= timedelta(minutes=15 * self.p.cooldown_bars)

    def evaluate(self, h4_df: pd.DataFrame, h1_df: pd.DataFrame, m15_df: pd.DataFrame) -> Optional[Setup]:
        p = self.p
        if h4_df.empty or h1_df.empty or m15_df.empty:
            return None
        if len(h4_df) < p.ema_period + 5 or len(h1_df) < p.ema_period + 5:
            return None  # недостаточно истории для EMA200 — ждём догрузки данных

        last_m15_time = m15_df["time"].iloc[-1]
        if self.last_processed_m15_time is not None and last_m15_time <= self.last_processed_m15_time:
            return None  # новый закрытый M15-бар ещё не появился
        self.last_processed_m15_time = last_m15_time

        # ── Блок 1: текущая M15-свеча ──
        c = m15_df.iloc[-1]
        prev = m15_df.iloc[-2]
        close, open_, high, low = c["close"], c["open"], c["high"], c["low"]
        candle_bull = close > open_
        candle_bear = close < open_
        body = abs(close - open_)
        rng = high - low
        upper_shadow = high - max(open_, close)
        lower_shadow = min(open_, close) - low
        prev_open, prev_close = prev["open"], prev["close"]
        prev_body = abs(prev_close - prev_open)

        # ── Блок 3/7: тренд по EMA200 H4+H1 (без репейнта — берём последний ЗАКРЫТЫЙ бар) ──
        ema_h4 = ema(h4_df["close"], p.ema_period).iloc[-1]
        ema_h1 = ema(h1_df["close"], p.ema_period).iloc[-1]
        atr14_h1 = atr(h1_df, p.atr_period).iloc[-1]
        atr14_m15 = atr(m15_df, p.atr_period).iloc[-1]

        flat = abs(ema_h4 - ema_h1) < atr14_h1 * p.flat_atr_mult
        if flat:
            trend_state = 0
        elif close > ema_h4 and close > ema_h1:
            trend_state = 1
        elif close < ema_h4 and close < ema_h1:
            trend_state = -1
        else:
            trend_state = 0
        trend_up = trend_state == 1
        trend_down = trend_state == -1

        # ── Блок 4: S/R — pivot high/low на H1 ──
        piv_high_idx, piv_low_idx = find_pivots(h1_df, p.sr_pivot_lr, p.sr_pivot_lr)
        pivot_highs = list(h1_df["high"].iloc[piv_high_idx])[-p.sr_max_levels:]
        pivot_lows = list(h1_df["low"].iloc[piv_low_idx])[-p.sr_max_levels:]

        nearest_resistance = nearest_above(pivot_highs, close)
        nearest_support = nearest_below(pivot_lows, close)

        pip = p.pip_size
        price_at_resistance = (
            nearest_resistance is not None and close <= nearest_resistance
            and (nearest_resistance - close) / pip <= p.sr_zone_pts
        )
        price_at_support = (
            nearest_support is not None and close >= nearest_support
            and (close - nearest_support) / pip <= p.sr_zone_pts
        )

        # ── Блок 5: пробой + ретест (state machine) ──
        broke_res_up = nearest_resistance is not None and close > nearest_resistance + p.breakout_buf_pts * pip
        broke_sup_down = nearest_support is not None and close < nearest_support - p.breakout_buf_pts * pip

        retest_long = False
        retest_short = False
        now = last_m15_time
        bars_since_pending = None
        if self.pending_dir != 0 and self.pending_breakout_time is not None:
            # считаем баров ТОЧНО по числу строк в окне (устойчиво к выходным-гэпам),
            # а не по разнице во времени / 15 минут
            matches = m15_df.index[m15_df["time"] == self.pending_breakout_time]
            if len(matches) > 0:
                bars_since_pending = (len(m15_df) - 1) - matches[0]
            else:
                bars_since_pending = p.retest_max_bars + 1  # пробой вышел за пределы окна — считаем истёкшим

        if self.pending_dir == 1 and bars_since_pending is not None and p.retest_min_bars <= bars_since_pending <= p.retest_max_bars:
            if close >= self.pending_level and (close - self.pending_level) / pip <= p.sr_zone_pts and candle_bull:
                retest_long = True
        if self.pending_dir == -1 and bars_since_pending is not None and p.retest_min_bars <= bars_since_pending <= p.retest_max_bars:
            if close <= self.pending_level and (self.pending_level - close) / pip <= p.sr_zone_pts and candle_bear:
                retest_short = True

        if self.pending_dir == 1 and close < self.pending_level - p.breakout_buf_pts * pip:
            self.pending_dir = 0
        if self.pending_dir == -1 and close > self.pending_level + p.breakout_buf_pts * pip:
            self.pending_dir = 0
        if self.pending_dir != 0 and bars_since_pending is not None and bars_since_pending > p.retest_max_bars:
            self.pending_dir = 0
        if retest_long or retest_short:
            self.pending_dir = 0
        if broke_res_up:
            self.pending_level = nearest_resistance
            self.pending_dir = 1
            self.pending_breakout_time = now
        if broke_sup_down:
            self.pending_level = nearest_support
            self.pending_dir = -1
            self.pending_breakout_time = now

        bounce_long = trend_up and price_at_support and candle_bull
        bounce_short = trend_down and price_at_resistance and candle_bear

        setup_long_trigger = retest_long or bounce_long
        setup_short_trigger = retest_short or bounce_short
        setup_long_type = "Пробой+Ретест" if retest_long else ("Отбой от S/R" if bounce_long else "нет")
        setup_short_type = "Пробой+Ретест" if retest_short else ("Отбой от S/R" if bounce_short else "нет")

        # ── Блок 8: свечные паттерны ──
        bullish_pin_bar = lower_shadow >= body * p.pin_bar_ratio and upper_shadow <= body * 0.5 and body > 0
        bearish_pin_bar = upper_shadow >= body * p.pin_bar_ratio and lower_shadow <= body * 0.5 and body > 0
        bullish_engulf = prev_close < prev_open and candle_bull and close > prev_open and open_ < prev_close and body > prev_body * 1.1
        bearish_engulf = prev_close > prev_open and candle_bear and close < prev_open and open_ > prev_close and body > prev_body * 1.1
        candle_dir = 1 if (bullish_pin_bar or bullish_engulf) else (-1 if (bearish_pin_bar or bearish_engulf) else 0)

        # ── Блок 6: объём (бонус) ──
        avg_vol = m15_df["volume"].rolling(p.vol_period).mean().iloc[-1]
        vol_ratio = (c["volume"] / avg_vol) if avg_vol and avg_vol > 0 else 0.0
        vol_ok = vol_ratio >= p.vol_mult

        # ── Блок 9: графические фигуры (двойная вершина/дно — вето) ──
        double_top = False
        double_bottom = False
        if len(pivot_highs) >= 2:
            h1_, h2_ = pivot_highs[-1], pivot_highs[-2]
            if abs(h1_ - h2_) / pip <= p.pattern_tol_pts and nearest_support is not None and close < nearest_support:
                double_top = True
        if len(pivot_lows) >= 2:
            l1_, l2_ = pivot_lows[-1], pivot_lows[-2]
            if abs(l1_ - l2_) / pip <= p.pattern_tol_pts and nearest_resistance is not None and close > nearest_resistance:
                double_bottom = True
        pattern_note = "⚠ Дв.вершина" if double_top else ("⚠ Дв.дно" if double_bottom else "нет")

        # ── Сборка сетапа: ядро + бонусы + вето ──
        core_long = trend_up and setup_long_trigger and candle_dir == 1
        core_short = trend_down and setup_short_trigger and candle_dir == -1

        bonus_long = (1 if vol_ok else 0) + (1 if double_bottom else 0)
        bonus_short = (1 if vol_ok else 0) + (1 if double_top else 0)

        long_ok = core_long and not double_top and bonus_long >= p.min_bonus
        short_ok = core_short and not double_bottom and bonus_short >= p.min_bonus

        long_signal = long_ok and self._cooldown_ok(self.last_long_time, now)
        short_signal = short_ok and self._cooldown_ok(self.last_short_time, now)

        if not long_signal and not short_signal:
            return None

        # ── Блок 10: вход/SL/TP (ATR-based) ──
        sl_dist = (atr14_m15 * p.atr_sl_mult) if p.use_atr else (p.sl_pts * pip)
        tp_dist = (sl_dist * p.rr) if p.use_atr else (p.tp_pts * pip)

        if long_signal:
            self.last_long_time = now
            return Setup(
                direction="LONG", setup_type=setup_long_type,
                entry=close, sl=close - sl_dist, tp=close + tp_dist,
                trend="UP", vol_ratio=round(vol_ratio, 2), vol_ok=vol_ok,
                pattern_note=pattern_note, bonus=bonus_long,
                time=now.to_pydatetime() if hasattr(now, "to_pydatetime") else now,
            )

        self.last_short_time = now
        return Setup(
            direction="SHORT", setup_type=setup_short_type,
            entry=close, sl=close + sl_dist, tp=close - tp_dist,
            trend="DOWN", vol_ratio=round(vol_ratio, 2), vol_ok=vol_ok,
            pattern_note=pattern_note, bonus=bonus_short,
            time=now.to_pydatetime() if hasattr(now, "to_pydatetime") else now,
        )
