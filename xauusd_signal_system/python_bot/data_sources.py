# -*- coding: utf-8 -*-
"""
Источники котировок золота — чистый Python (только stdlib + requests, БЕЗ
numpy/pandas/yfinance). Так бот гарантированно работает на любом железе,
включая старые CPU без SSE4.1/AVX (где готовые сборки numpy/pandas могут
падать с "Illegal instruction").

Свеча — обычный dict: {"time": datetime (UTC), "open", "high", "low",
"close", "volume"}. Функции возвращают list[dict], отсортированный по
времени, ТОЛЬКО полностью закрытые свечи (текущая незакрытая всегда
отбрасывается).

Два варианта:
- YahooChartSource — без регистрации, напрямую дёргает публичный chart API
  Yahoo Finance (тот же, что использует библиотека yfinance внутри, но без
  тяжёлых зависимостей). Тикер по умолчанию "GC=F" (фьючерс на золото).
- OandaSource — нужен бесплатный демо-счёт OANDA, данные брокерские.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

GRANULARITY_MINUTES = {"M15": 15, "H1": 60, "H4": 240}

_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def _parse_iso(ts: str) -> datetime:
    """Парсит ISO-время OANDA (может быть с наносекундами) в datetime UTC."""
    ts = re.sub(r"(\.\d{6})\d+", r"\1", ts)  # обрезаем до микросекунд
    ts = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(ts)


def _keep_closed_only(candles: list[dict], granularity: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    minutes = GRANULARITY_MINUTES[granularity]
    return [c for c in candles if c["time"] + timedelta(minutes=minutes) <= now]


def resample_h1_to_h4(h1_candles: list[dict]) -> list[dict]:
    """Группирует H1-свечи по 4-часовым блокам (UTC), выровненным на 00/04/08...ч."""
    buckets: dict[datetime, list[dict]] = {}
    for c in h1_candles:
        bucket_hour = (c["time"].hour // 4) * 4
        bucket_time = c["time"].replace(hour=bucket_hour, minute=0, second=0, microsecond=0)
        buckets.setdefault(bucket_time, []).append(c)

    out = []
    for bucket_time in sorted(buckets.keys()):
        rows = buckets[bucket_time]
        out.append({
            "time": bucket_time,
            "open": rows[0]["open"],
            "high": max(r["high"] for r in rows),
            "low": min(r["low"] for r in rows),
            "close": rows[-1]["close"],
            "volume": sum(r["volume"] for r in rows),
        })
    return out


class OandaSource:
    def __init__(self, api_key: str, instrument: str = "XAU_USD", environment: str = "practice"):
        self.api_key = api_key
        self.instrument = instrument
        self.base_url = (
            "https://api-fxpractice.oanda.com" if environment == "practice"
            else "https://api-fxtrade.oanda.com"
        )

    def get_candles(self, granularity: str, count: int) -> list[dict]:
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
                "time": _parse_iso(c["time"]),
                "open": float(c["mid"]["o"]),
                "high": float(c["mid"]["h"]),
                "low": float(c["mid"]["l"]),
                "close": float(c["mid"]["c"]),
                "volume": float(c["volume"]),
            })
        return rows


class YahooChartSource:
    GRANULARITY_TO_INTERVAL = {"M15": "15m", "H1": "60m"}

    def __init__(self, ticker: str = "GC=F"):
        self.ticker = ticker

    def get_candles(self, granularity: str, count: int) -> list[dict]:
        if granularity == "H4":
            h1 = self._fetch_raw("H1")
            candles = resample_h1_to_h4(h1)
        else:
            candles = self._fetch_raw(granularity)

        candles = _keep_closed_only(candles, granularity)
        return candles[-count:]

    def _fetch_raw(self, granularity: str) -> list[dict]:
        interval = self.GRANULARITY_TO_INTERVAL[granularity]
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{self.ticker}"
        params = {"interval": interval, "range": "60d"}
        resp = requests.get(url, params=params, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        result = (data.get("chart") or {}).get("result")
        if not result:
            error = (data.get("chart") or {}).get("error")
            raise RuntimeError(f"Yahoo Finance не вернул данные для {self.ticker}: {error}")

        r0 = result[0]
        timestamps = r0.get("timestamp") or []
        quote = (r0.get("indicators") or {}).get("quote", [{}])[0]
        opens = quote.get("open") or []
        highs = quote.get("high") or []
        lows = quote.get("low") or []
        closes = quote.get("close") or []
        volumes = quote.get("volume") or []

        rows = []
        for i, ts in enumerate(timestamps):
            o, h, l, c = opens[i], highs[i], lows[i], closes[i]
            if o is None or h is None or l is None or c is None:
                continue  # Yahoo отдаёт null на пропущенных барах (выходные/разрывы)
            rows.append({
                "time": datetime.fromtimestamp(ts, tz=timezone.utc),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0.0,
            })
        return rows


def build_data_source(source_name: str, **kwargs):
    if source_name == "oanda":
        return OandaSource(
            api_key=kwargs["oanda_api_key"],
            instrument=kwargs.get("oanda_instrument", "XAU_USD"),
            environment=kwargs.get("oanda_environment", "practice"),
        )
    if source_name in ("yahoo", "yfinance"):
        return YahooChartSource(ticker=kwargs.get("yfinance_ticker", "GC=F"))
    raise ValueError(f"Неизвестный источник данных: {source_name}")
