"""Ingest: pull price and volume history from Polymarket and Kalshi into the local cache.

Every venue client returns the same shape so the detector never cares where data came from:
    DataFrame indexed by UTC timestamp, columns `price` (0..1) and `volume` (may be NaN).

NOTE: the endpoints below were checked against the official docs only; live
calls were blocked in the sandbox this scaffold was written in. Run
`python scripts/smoke_test_apis.py` first and fix field names if a response
doesn't match.
"""

from __future__ import annotations

import pandas as pd

from . import kalshi, polymarket


def fetch(market: dict, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Fetch one configured market (an entry from config/markets.yaml)."""
    if market["venue"] == "polymarket":
        return polymarket.history(market["token_id"], start, end)
    if market["venue"] == "kalshi":
        return kalshi.candlesticks(market["series_ticker"], market["ticker"], start, end)
    raise ValueError(f"unknown venue {market['venue']!r}")


def to_grid(df: pd.DataFrame, freq: str = "1min") -> pd.DataFrame:
    """Put a series on a regular grid: price carried forward, volume summed (0 where no trades)."""
    if df.empty:
        return df
    price = df["price"].resample(freq).last().ffill()
    volume = df["volume"].resample(freq).sum(min_count=1)
    if df["volume"].notna().any():
        volume = volume.fillna(0)
    return pd.DataFrame({"price": price, "volume": volume}).dropna(subset=["price"])
