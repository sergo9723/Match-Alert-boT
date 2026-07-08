# -*- coding: utf-8 -*-
"""
Даты заседаний FOMC (решение по ставке ФРС США) — из официального
календаря federalreserve.gov, дата второго дня заседания (день публикации
решения, 14:00 по времени Нью-Йорка).

Бэктест на 3.5 годах XAUUSD показал: в день FOMC размах в час 21:00-22:00
времени сервера MT5 (вывод: сервер = ET+7ч, час публикации решения) в
2.5-3.8 раза больше обычного — резкое, но ненаправленное движение (50/50
лонг/шорт). NFP (нонфарм, "первая пятница месяца") заметного эффекта на
золото в этих данных не показал — сюда не включён.
"""
from __future__ import annotations

import datetime

FOMC_DATES = [
    datetime.date(2023, 2, 1), datetime.date(2023, 3, 22), datetime.date(2023, 5, 3),
    datetime.date(2023, 6, 14), datetime.date(2023, 7, 26), datetime.date(2023, 9, 20),
    datetime.date(2023, 11, 1), datetime.date(2023, 12, 13),
    datetime.date(2024, 1, 31), datetime.date(2024, 3, 20), datetime.date(2024, 5, 1),
    datetime.date(2024, 6, 12), datetime.date(2024, 7, 31), datetime.date(2024, 9, 18),
    datetime.date(2024, 11, 7), datetime.date(2024, 12, 18),
    datetime.date(2025, 1, 29), datetime.date(2025, 3, 19), datetime.date(2025, 5, 7),
    datetime.date(2025, 6, 18), datetime.date(2025, 7, 30), datetime.date(2025, 9, 17),
    datetime.date(2025, 10, 29), datetime.date(2025, 12, 10),
    datetime.date(2026, 1, 28), datetime.date(2026, 3, 18), datetime.date(2026, 4, 29),
    datetime.date(2026, 6, 17), datetime.date(2026, 7, 29),
]


def fomc_blackout(hours: tuple[int, ...] = (21, 22, 23)) -> frozenset[tuple]:
    """(дата, час) пары — вход запрещён в эти часы в дни FOMC."""
    return frozenset((d, h) for d in FOMC_DATES for h in hours)
