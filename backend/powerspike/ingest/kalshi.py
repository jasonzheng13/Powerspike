"""Kalshi client (no auth needed for market data).

Live-tested 2026-10-09:
  Base: https://api.elections.kalshi.com/trade-api/v2   (covers all Kalshi markets)
  GET /markets?series_ticker=...&status=open
  GET /series/{series_ticker}/markets/{ticker}/candlesticks
      params: start_ts, end_ts (unix s), period_interval = 1 | 60 | 1440 (minutes)
      returns {"candlesticks": [{"end_period_ts", "price": {"close_dollars", ...}, "yes_bid": {...}, "volume_fp", ...}]}
      Prices are dollar strings ("0.4600"), volume a string ("120.00"). At most 5000 candles per request.
      Older responses used integer cents (`close`) and `volume`; both shapes are accepted.
"""

from __future__ import annotations

import httpx
import pandas as pd

BASE = "https://api.elections.kalshi.com/trade-api/v2"
MAX_CANDLES = 5000


def list_markets(series_ticker: str, status: str = "open") -> list[dict]:
    r = httpx.get(f"{BASE}/markets", params={"series_ticker": series_ticker, "status": status, "limit": 200}, timeout=30)
    r.raise_for_status()
    return [{"ticker": m["ticker"], "title": m.get("title"), "volume": m.get("volume_fp", m.get("volume"))} for m in r.json().get("markets", [])]


def _close(side: dict | None) -> float | None:
    """Close as a 0..1 probability, from `close_dollars` or legacy `close` in cents."""
    side = side or {}
    if side.get("close_dollars") is not None:
        return float(side["close_dollars"])
    if side.get("close") is not None:
        return side["close"] / 100.0
    return None


def parse_candles(candles: list[dict]) -> list[dict]:
    """Candle JSON -> rows of ts / price / volume.

    Price is the last trade when the candle had one, else the bid/ask midpoint. A one-sided or
    empty book (bid 0 or ask 1, which is what Kalshi reports once a market has closed) has no
    midpoint worth the name, so those candles are dropped rather than written as 50%.
    """
    rows = []
    for c in candles:
        close = _close(c.get("price"))
        if close is None:
            bid, ask = _close(c.get("yes_bid")), _close(c.get("yes_ask"))
            if bid is None or ask is None or bid <= 0 or ask >= 1:
                continue
            close = (bid + ask) / 2
        volume = float(c.get("volume_fp", c.get("volume")) or 0)
        rows.append({"ts": pd.to_datetime(c["end_period_ts"], unit="s", utc=True), "price": close, "volume": volume})
    return rows


def candlesticks(series_ticker: str, ticker: str, start: pd.Timestamp, end: pd.Timestamp, period: int = 1) -> pd.DataFrame:
    rows = []
    step = pd.Timedelta(minutes=period * MAX_CANDLES)
    t0 = start
    while t0 < end:
        t1 = min(t0 + step, end)
        params = {"start_ts": int(t0.timestamp()), "end_ts": int(t1.timestamp()), "period_interval": period}
        r = httpx.get(f"{BASE}/series/{series_ticker}/markets/{ticker}/candlesticks", params=params, timeout=30)
        r.raise_for_status()
        rows.extend(parse_candles(r.json().get("candlesticks", [])))
        t0 = t1
    if not rows:
        return pd.DataFrame(columns=["price", "volume"])
    df = pd.DataFrame(rows).set_index("ts")
    return df[~df.index.duplicated(keep="last")].sort_index()
