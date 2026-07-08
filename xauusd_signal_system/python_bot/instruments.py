# -*- coding: utf-8 -*-
"""
Реестр торговых инструментов для многоинструментного запуска бота.

Один и тот же фильтр качества (min_bonus=1, require_liquidity_sweep=True —
см. strategy.StrategyParams) применяется параллельно к нескольким
инструментам вместо ослабления фильтра на одном золоте — так частота
сигналов растёт БЕЗ потери качества каждой отдельной сделки (речь идёт
о диверсификации, а не о новом рычаге точности).

⚠️ pip_size здесь подобран по стандартной котировке инструмента (форекс-
пары — 4 знака, кроме JPY — 2 знака, золото — 2 знака), но НЕ бэктестился
отдельно для каждой пары так, как золото. Прежде чем доверять сигналам по
новому инструменту деньгами — прогоните backtest.py на его собственной
истории из MT5, как мы делали для XAUUSD.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class InstrumentConfig:
    label: str            # для сообщений/логов, напр. "XAUUSD"
    yahoo_ticker: str      # тикер для Yahoo Finance chart API
    oanda_instrument: str  # код инструмента для OANDA API
    pip_size: float        # 1 pt в единицах цены (см. StrategyParams.pip_size)


REGISTRY: dict[str, InstrumentConfig] = {
    "XAUUSD": InstrumentConfig("XAUUSD", "GC=F", "XAU_USD", 0.01),
    "EURUSD": InstrumentConfig("EURUSD", "EURUSD=X", "EUR_USD", 0.0001),
    "GBPUSD": InstrumentConfig("GBPUSD", "GBPUSD=X", "GBP_USD", 0.0001),
    "USDJPY": InstrumentConfig("USDJPY", "USDJPY=X", "USD_JPY", 0.01),
    "AUDUSD": InstrumentConfig("AUDUSD", "AUDUSD=X", "AUD_USD", 0.0001),
    "USDCAD": InstrumentConfig("USDCAD", "USDCAD=X", "USD_CAD", 0.0001),
    "NZDUSD": InstrumentConfig("NZDUSD", "NZDUSD=X", "NZD_USD", 0.0001),
    "USDCHF": InstrumentConfig("USDCHF", "USDCHF=X", "USD_CHF", 0.0001),
    "XAGUSD": InstrumentConfig("XAGUSD", "SI=F", "XAG_USD", 0.001),
}


def parse_instruments_env(value: str) -> list[InstrumentConfig]:
    """'XAUUSD,EURUSD,GBPUSD' -> [InstrumentConfig, ...]. Неизвестные метки пропускаются с предупреждением."""
    labels = [x.strip().upper() for x in value.split(",") if x.strip()]
    result = []
    for label in labels:
        cfg = REGISTRY.get(label)
        if cfg is None:
            print(f"⚠️ Неизвестный инструмент '{label}' в INSTRUMENTS — пропускаю. "
                  f"Доступные: {', '.join(REGISTRY.keys())}")
            continue
        result.append(cfg)
    return result
