# -*- coding: utf-8 -*-
"""
Источники котировок золота для сигнального бота.

Два варианта "куда бот смотрит за ценой", без TradingView:

- YFinanceSource — без регистрации, работает сразу из коробки. Тикер по
  умолчанию "GC=F" (фьючерс на золото COMEX) — у него надёжнее внутридневные
  данные на Yahoo Finance, чем у спотового "XAUUSD=X". Ограничение: минутные
  данные хранятся у Yahoo только за последние ~60 дней и иногда бывают
  небольшие разрывы — для сигнального (не HFT) бота этого достаточно.

- OandaSource — нужен бесплатный демо-счёт OANDA (fxtrade practice),
  но данные "настоящие" брокерские (bid/mid/ask), без разрывов, плюс H4
  отдаётся напрямую (не нужно строить из H1).

Оба возвращают pandas.DataFrame с колонками time/open/high/low/close/volume,
отсортированный по времени, содержащий ТОЛЬКО полностью закрытые свечи
(текущая незакрытая свеча всегда отбрасывается — иначе сигнал считался бы
по недосчитанным данным, как было в баге со старым Pine-скриптом).
"""
from __future__ import annotations

import pandas as pd
import requests

GRANULARITY_MINUTES = {"M15": 15, "H1": 60, "H4": 240}


def resample_h1_to_h4(h1_df: pd.DataFrame) -> pd.DataFrame:
    if h1_df.empty:
        return h1_df
    df = h1_df.set_index("time")
    agg = pd.DataFrame({
        "open": df["open"].resample("4h").first(),
        "high": df["high"].resample("4h").max(),
        "low": df["low"].resample("4h").min(),
        "close": df["close"].resample("4h").last(),
        "volume": df["volume"].resample("4h").sum(),
    }).dropna()
    return agg.reset_index()


class OandaSource:
    def __init__(self, api_key: str, instrument: str = "XAU_USD", environment: str = "practice"):
        self.api_key = api_key
        self.instrument = instrument
        self.base_url = (
            "https://api-fxpractice.oanda.com" if environment == "practice"
            else "https://api-fxtrade.oanda.com"
        )

    def get_candles(self, granularity: str, count: int) -> pd.DataFrame:
        url = f"{self.base_url}/v3/instruments/{self.instrument}/candles"
        params = {"granularity": granularity, "count": count, "price": "M"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        rows = []
        for c in resp.json().get("candles", []):
            if not c.get("complete"):
                continue
            rows.append({
                "time": pd.to_datetime(c["time"], utc=True),
                "open": float(c["mid"]["o"]),
                "high": float(c["mid"]["h"]),
                "low": float(c["mid"]["l"]),
                "close": float(c["mid"]["c"]),
                "volume": float(c["volume"]),
            })
        return pd.DataFrame(rows)


class YFinanceSource:
    GRANULARITY_TO_INTERVAL = {"M15": "15m", "H1": "60m"}

    def __init__(self, ticker: str = "GC=F"):
        self.ticker = ticker

    def get_candles(self, granularity: str, count: int) -> pd.DataFrame:
        if granularity == "H4":
            h1 = self._fetch_raw("H1")
            df = resample_h1_to_h4(h1)
        else:
            df = self._fetch_raw(granularity)

        if df.empty:
            return df

        now = pd.Timestamp.now(tz="UTC")
        minutes = GRANULARITY_MINUTES[granularity]
        df = df[df["time"] + pd.Timedelta(minutes=minutes) <= now]
        return df.tail(count).reset_index(drop=True)

    def _fetch_raw(self, granularity: str) -> pd.DataFrame:
        import yfinance as yf

        interval = self.GRANULARITY_TO_INTERVAL[granularity]
        period = "60d"
        data = yf.download(self.ticker, interval=interval, period=period, progress=False)
        if data.empty:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)

        data = data.reset_index()
        time_col = "Datetime" if "Datetime" in data.columns else "Date"
        return pd.DataFrame({
            "time": pd.to_datetime(data[time_col], utc=True),
            "open": data["Open"].astype(float),
            "high": data["High"].astype(float),
            "low": data["Low"].astype(float),
            "close": data["Close"].astype(float),
            "volume": data["Volume"].astype(float),
        })


def build_data_source(source_name: str, **kwargs):
    if source_name == "oanda":
        return OandaSource(
            api_key=kwargs["oanda_api_key"],
            instrument=kwargs.get("oanda_instrument", "XAU_USD"),
            environment=kwargs.get("oanda_environment", "practice"),
        )
    if source_name == "yfinance":
        return YFinanceSource(ticker=kwargs.get("yfinance_ticker", "GC=F"))
    raise ValueError(f"Неизвестный источник данных: {source_name}")
