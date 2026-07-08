# -*- coding: utf-8 -*-
"""
Прямой порт логики XAUUSD_TA_Master_v6.pine на чистый Python (без
numpy/pandas — только stdlib), чтобы бот гарантированно работал на любом
процессоре, включая старые CPU без SSE4.1/AVX, где готовые сборки
numpy/pandas падают с "Illegal instruction".

Один сетап = тренд (Блок 3/7) + триггер (Блок 5: пробой+ретест ИЛИ
liquidity sweep/отбой от уровня по тренду, Блок 4) + свечное подтверждение
(Блок 8), без встречной графической фигуры (Блок 9) и без CHoCH против
направления сделки. Объём, попадание в FVG (imbalance) — бонус к уверенности.

SMC-дополнения:
- Liquidity sweep — цена протыкает уровень тенью (собирает стопы) и
  закрывается обратно за него: признак ложного пробоя, сильнее простого
  "отбоя от уровня".
- FVG (Fair Value Gap) — 3-свечная модель имбаланса, бонус к уверенности,
  если цена сейчас в зоне недавнего непокрытого гэпа по направлению сделки.
- CHoCH (Change of Character) — слом структуры (HH/HL или LH/LL) на H1;
  вето против входа в сторону, откуда только что пришёл сигнал разворота,
  даже если EMA200 ещё не успела это подтвердить.

Свеча — dict {"time": datetime, "open", "high", "low", "close", "volume"}.

Состояние (ожидание ретеста после пробоя, антиспам-кулдаун) хранится в
атрибутах XAUStrategy и переживает между вызовами evaluate() — как `var`
в Pine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass
class StrategyParams:
    entry_tf_minutes: int = 15  # реальная длина бара входного таймфрейма (M15=15, M5=5) — влияет на кулдаун

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

    # Бэктест на 3.5 годах XAUUSD (2023-01 — 2026-07) показал: "Отбой от S/R"
    # (простая близость к уровню) даёт винрейт НИЖЕ безубытка при RR=2
    # (31.2% против нужных 33.3%), а "Liquidity Sweep" (протыкание уровня
    # тенью + закрытие обратно) — выше (36.2%). require_liquidity_sweep=True
    # отключает слабый вариант и оставляет только Liquidity Sweep.
    require_liquidity_sweep: bool = False

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


def ema_last(values: list[float], period: int) -> Optional[float]:
    """Последнее значение EMA (adjust=False, как в pandas .ewm)."""
    if not values:
        return None
    k = 2.0 / (period + 1)
    result = values[0]
    for v in values[1:]:
        result = v * k + result * (1 - k)
    return result


def atr_last(candles: list[dict], period: int) -> Optional[float]:
    """Последнее значение ATR — простое среднее True Range за последние period баров."""
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h, l = candles[i]["high"], candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    tail = trs[-period:]
    return sum(tail) / len(tail)


def find_pivots(candles: list[dict], left: int, right: int) -> tuple[list[int], list[int]]:
    """Индексы подтверждённых pivot high / pivot low (симметрично left/right)."""
    n = len(candles)
    piv_high, piv_low = [], []
    for i in range(left, n - right):
        window = candles[i - left:i + right + 1]
        hi = candles[i]["high"]
        lo = candles[i]["low"]
        window_highs = [c["high"] for c in window]
        window_lows = [c["low"] for c in window]
        if hi == max(window_highs) and window_highs.count(hi) == 1:
            piv_high.append(i)
        if lo == min(window_lows) and window_lows.count(lo) == 1:
            piv_low.append(i)
    return piv_high, piv_low


def nearest_above(levels: list[float], price: float) -> Optional[float]:
    candidates = [lv for lv in levels if lv > price]
    return min(candidates) if candidates else None


def nearest_below(levels: list[float], price: float) -> Optional[float]:
    candidates = [lv for lv in levels if lv < price]
    return max(candidates) if candidates else None


def find_recent_fvg_zones(candles: list[dict], lookback: int = 30) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Imbalance / Fair Value Gap: 3-свечная модель, где тело средней свечи
    оставляет непокрытый разрыв между тенями свечи 1 и свечи 3. Бычий FVG —
    потенциальная зона докупки на откате внутри аптренда, медвежий — зеркально."""
    bull_zones, bear_zones = [], []
    start = max(2, len(candles) - lookback)
    for i in range(start, len(candles)):
        a, c = candles[i - 2], candles[i]
        if c["low"] > a["high"]:
            bull_zones.append((a["high"], c["low"]))
        if c["high"] < a["low"]:
            bear_zones.append((c["high"], a["low"]))
    return bull_zones, bear_zones


def price_in_zone(price: float, zones: list[tuple[float, float]]) -> bool:
    return any(lo <= price <= hi for lo, hi in zones)


def detect_choch(pivot_highs: list[float], pivot_lows: list[float], close: float) -> tuple[bool, bool]:
    """CHoCH (Change of Character) — ранний сигнал слома структуры тренда:
    bearish_choch = структура делала растущие лои (HL), но цена только что
    пробила последний лой вниз — восходящая структура ломается.
    bullish_choch = структура делала падающие хаи (LH), но цена пробила
    последний хай вверх — нисходящая структура ломается."""
    bearish_choch = len(pivot_lows) >= 2 and pivot_lows[-1] > pivot_lows[-2] and close < pivot_lows[-1]
    bullish_choch = len(pivot_highs) >= 2 and pivot_highs[-1] < pivot_highs[-2] and close > pivot_highs[-1]
    return bullish_choch, bearish_choch


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
        return now - last_time >= timedelta(minutes=self.p.entry_tf_minutes * self.p.cooldown_bars)

    def evaluate(self, h4: list[dict], h1: list[dict], m15: list[dict]) -> Optional[Setup]:
        p = self.p
        if not h4 or not h1 or not m15:
            return None
        if len(h4) < p.ema_period + 5 or len(h1) < p.ema_period + 5:
            return None  # недостаточно истории для EMA200 — ждём догрузки данных

        last_m15_time = m15[-1]["time"]
        if self.last_processed_m15_time is not None and last_m15_time <= self.last_processed_m15_time:
            return None  # новый закрытый M15-бар ещё не появился
        self.last_processed_m15_time = last_m15_time

        # ── Блок 1: текущая M15-свеча ──
        c = m15[-1]
        prev = m15[-2]
        close, open_, high, low = c["close"], c["open"], c["high"], c["low"]
        candle_bull = close > open_
        candle_bear = close < open_
        body = abs(close - open_)
        upper_shadow = high - max(open_, close)
        lower_shadow = min(open_, close) - low
        prev_open, prev_close = prev["open"], prev["close"]
        prev_body = abs(prev_close - prev_open)

        # ── Блок 3/7: тренд по EMA200 H4+H1 (без репейнта — последний ЗАКРЫТЫЙ бар) ──
        ema_h4 = ema_last([x["close"] for x in h4], p.ema_period)
        ema_h1 = ema_last([x["close"] for x in h1], p.ema_period)
        atr14_h1 = atr_last(h1, p.atr_period)
        atr14_m15 = atr_last(m15, p.atr_period)
        if ema_h4 is None or ema_h1 is None or atr14_h1 is None or atr14_m15 is None:
            return None

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
        piv_high_idx, piv_low_idx = find_pivots(h1, p.sr_pivot_lr, p.sr_pivot_lr)
        pivot_highs = [h1[i]["high"] for i in piv_high_idx][-p.sr_max_levels:]
        pivot_lows = [h1[i]["low"] for i in piv_low_idx][-p.sr_max_levels:]

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
            idx = next((i for i, x in enumerate(m15) if x["time"] == self.pending_breakout_time), None)
            bars_since_pending = (len(m15) - 1 - idx) if idx is not None else (p.retest_max_bars + 1)

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

        # Liquidity sweep (SMC, "ложный пробой"/стоп-хант): цена протыкает
        # уровень тенью (собирает стопы/ликвидность за ним), но ЗАКРЫВАЕТСЯ
        # обратно за уровнем — признак ложного пробоя и разворота в сторону
        # тренда, а не начала нового движения. Сильнее, чем просто "цена
        # рядом с уровнем", поэтому проверяется первым.
        swept_support = nearest_support is not None and low < nearest_support and close > nearest_support
        swept_resistance = nearest_resistance is not None and high > nearest_resistance and close < nearest_resistance

        liquidity_sweep_long = trend_up and swept_support and candle_bull
        liquidity_sweep_short = trend_down and swept_resistance and candle_bear

        weak_bounce_long = trend_up and price_at_support and candle_bull
        weak_bounce_short = trend_down and price_at_resistance and candle_bear

        bounce_long = liquidity_sweep_long if p.require_liquidity_sweep else (liquidity_sweep_long or weak_bounce_long)
        bounce_short = liquidity_sweep_short if p.require_liquidity_sweep else (liquidity_sweep_short or weak_bounce_short)

        setup_long_trigger = retest_long or bounce_long
        setup_short_trigger = retest_short or bounce_short
        setup_long_type = (
            "Пробой+Ретест" if retest_long else
            "Liquidity Sweep" if liquidity_sweep_long else
            "Отбой от S/R" if bounce_long else "нет"
        )
        setup_short_type = (
            "Пробой+Ретест" if retest_short else
            "Liquidity Sweep" if liquidity_sweep_short else
            "Отбой от S/R" if bounce_short else "нет"
        )

        # ── Блок 8: свечные паттерны ──
        bullish_pin_bar = lower_shadow >= body * p.pin_bar_ratio and upper_shadow <= body * 0.5 and body > 0
        bearish_pin_bar = upper_shadow >= body * p.pin_bar_ratio and lower_shadow <= body * 0.5 and body > 0
        bullish_engulf = prev_close < prev_open and candle_bull and close > prev_open and open_ < prev_close and body > prev_body * 1.1
        bearish_engulf = prev_close > prev_open and candle_bear and close < prev_open and open_ > prev_close and body > prev_body * 1.1
        candle_dir = 1 if (bullish_pin_bar or bullish_engulf) else (-1 if (bearish_pin_bar or bearish_engulf) else 0)

        # ── Блок 6: объём (бонус) ──
        vol_window = [x["volume"] for x in m15[-p.vol_period:]]
        avg_vol = (sum(vol_window) / len(vol_window)) if vol_window else 0.0
        vol_ratio = (c["volume"] / avg_vol) if avg_vol > 0 else 0.0
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

        # ── Imbalance / Fair Value Gap на M15 (бонус) ──
        bull_fvg_zones, bear_fvg_zones = find_recent_fvg_zones(m15, lookback=30)
        in_bull_fvg = price_in_zone(close, bull_fvg_zones)
        in_bear_fvg = price_in_zone(close, bear_fvg_zones)
        if in_bull_fvg:
            pattern_note = (pattern_note + " | FVG") if pattern_note != "нет" else "FVG"
        if in_bear_fvg:
            pattern_note = (pattern_note + " | FVG") if pattern_note != "нет" else "FVG"

        # ── CHoCH — слом структуры тренда на H1 (вето против входа в сторону
        # зарождающегося разворота, который EMA200 ещё не подтвердил) ──
        bullish_choch, bearish_choch = detect_choch(pivot_highs, pivot_lows, close)
        if bearish_choch:
            pattern_note = (pattern_note + " | ⚠CHoCH↓") if pattern_note != "нет" else "⚠CHoCH↓"
        if bullish_choch:
            pattern_note = (pattern_note + " | ⚠CHoCH↑") if pattern_note != "нет" else "⚠CHoCH↑"

        # ── Сборка сетапа: ядро + бонусы + вето ──
        core_long = trend_up and setup_long_trigger and candle_dir == 1
        core_short = trend_down and setup_short_trigger and candle_dir == -1

        bonus_long = (1 if vol_ok else 0) + (1 if double_bottom else 0) + (1 if in_bull_fvg else 0)
        bonus_short = (1 if vol_ok else 0) + (1 if double_top else 0) + (1 if in_bear_fvg else 0)

        long_ok = core_long and not double_top and not bearish_choch and bonus_long >= p.min_bonus
        short_ok = core_short and not double_bottom and not bullish_choch and bonus_short >= p.min_bonus

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
                pattern_note=pattern_note, bonus=bonus_long, time=now,
            )

        self.last_short_time = now
        return Setup(
            direction="SHORT", setup_type=setup_short_type,
            entry=close, sl=close + sl_dist, tp=close - tp_dist,
            trend="DOWN", vol_ratio=round(vol_ratio, 2), vol_ok=vol_ok,
            pattern_note=pattern_note, bonus=bonus_short, time=now,
        )
