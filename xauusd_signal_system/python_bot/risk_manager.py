# -*- coding: utf-8 -*-
"""
Риск-менеджмент поверх сигналов XAUStrategy: считает лот под реальный
риск% от баланса (вместо ручного угадывания лота), ограничивает число
новых сигналов в день, и ставит паузу на неделю, если недельная просадка
превысила лимит (защита от "плохая неделя стала катастрофической").

Бэктест (300 сделок, RR=2.5+FOMC-blackout+cooldown=3, см. README) показал:
только 52.5% недель со сделками закрылись в плюс — при экспектанси
+0.272R/сделку это нормально (закон больших чисел работает на дистанции
месяцев, не гарантирует плюс к пятнице). Недельный лимит просадки не
делает стратегию прибыльнее — он не даёт одной плохой неделе съесть
депозит, пока едет полоса невезения.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

OZ_PER_STANDARD_LOT = 100  # 1.00 лот XAUUSD = 100 унций
MIN_LOT_STEP = 0.01


@dataclass
class RiskManager:
    balance: float
    risk_pct: float = 1.0
    max_trades_per_day: int = 3
    weekly_loss_limit_r: float | None = 6.0
    min_lot: float = 0.01

    _day_count: dict = field(default_factory=dict)
    _week_r: dict = field(default_factory=dict)

    def lot_size(self, entry: float, sl: float, pip_size: float) -> float:
        """Лот под риск risk_pct% от текущего баланса на конкретную дистанцию SL."""
        sl_dist = abs(entry - sl)
        if sl_dist <= 0:
            return 0.0
        risk_amount = self.balance * self.risk_pct / 100
        oz = risk_amount / sl_dist
        lot = oz / OZ_PER_STANDARD_LOT
        lot = round(lot / MIN_LOT_STEP) * MIN_LOT_STEP
        return max(lot, self.min_lot) if lot > 0 else 0.0

    def can_open(self, now: datetime) -> tuple[bool, str]:
        day_key = now.date()
        week_key = now.isocalendar()[:2]
        if self._day_count.get(day_key, 0) >= self.max_trades_per_day:
            return False, f"дневной лимит {self.max_trades_per_day} сигналов уже достигнут сегодня"
        if self.weekly_loss_limit_r is not None and self._week_r.get(week_key, 0.0) <= -self.weekly_loss_limit_r:
            return False, f"недельная просадка достигла -{self.weekly_loss_limit_r}R — пауза до следующей недели"
        return True, ""

    def record_signal(self, now: datetime) -> None:
        day_key = now.date()
        self._day_count[day_key] = self._day_count.get(day_key, 0) + 1

    def record_result(self, now: datetime, r_multiple: float) -> float:
        """Записывает исход закрывшейся сделки в R-мультипликаторах, возвращает
        накопленный R за текущую неделю."""
        week_key = now.isocalendar()[:2]
        total = self._week_r.get(week_key, 0.0) + r_multiple
        self._week_r[week_key] = total
        return total
