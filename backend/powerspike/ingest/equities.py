"""Equity (ETF) minute bars for the lead/lag question, in the same bar frame as the venues.

Same contract as polymarket.py / kalshi.py: a DataFrame indexed by UTC timestamp with
columns `price` and `volume`, or an empty frame when there is no data. Prices are dollars,
not probabilities, so `leadlag.py` works in log price rather than log-odds.

Stocks trade 09:30-16:00 America/New_York on weekdays, while prediction markets trade
around the clock. `session_grid` therefore applies `to_grid` *per session* so a price is
never forward-filled across the overnight or weekend gap, and `next_open` / `in_session`
let lead/lag say "measured from the next open" for an after-hours spike.

Historical source: yfinance 1-minute bars (only the last ~30 days exist, max 7 days per
request). A live source (Alpaca) slots in behind the same `shape_*` + `session_grid` path.
Holidays are ignored: a market holiday simply has no bars and no reaction is found.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

NY = "America/New_York"
OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)
EMPTY = pd.DataFrame(columns=["price", "volume"])


def equity_id(ticker: str) -> str:
    """Cache key for an ETF's bars; the prefix keeps it out of `markets.yaml` lookups."""
    return f"equity:{ticker.upper()}"


def in_session(ts: pd.Timestamp) -> bool:
    t = pd.Timestamp(ts).tz_convert(NY)
    return t.weekday() < 5 and OPEN <= t.time() < CLOSE


def next_open(ts: pd.Timestamp) -> pd.Timestamp:
    """The first regular-session minute at or after `ts` (itself when already in session)."""
    t = pd.Timestamp(ts).tz_convert(NY)
    if in_session(t):
        return pd.Timestamp(ts)
    day = t.normalize()
    if t.time() >= CLOSE or t.weekday() >= 5:
        day += pd.Timedelta(days=1)
    while day.weekday() >= 5:
        day += pd.Timedelta(days=1)
    return (day + pd.Timedelta(hours=OPEN.hour, minutes=OPEN.minute)).tz_convert("UTC")


def session_grid(df: pd.DataFrame, freq: str = "1min") -> pd.DataFrame:
    """`to_grid` applied within each NYSE session, so nothing is filled across the gap."""
    from . import to_grid  # local import: ingest/__init__ imports this module

    if df.empty:
        return df
    local = df.index.tz_convert(NY)
    keep = df[(local.weekday < 5) & (local.time >= OPEN) & (local.time < CLOSE)]
    if keep.empty:
        return EMPTY.copy()
    parts = [to_grid(g, freq) for _, g in keep.groupby(keep.index.tz_convert(NY).date)]
    return pd.concat(parts).sort_index()


def shape_yfinance(raw: pd.DataFrame) -> pd.DataFrame:
    """yfinance OHLCV (exchange-local index) -> bar frame (UTC index, price=Close, volume=Volume)."""
    if raw.empty:
        return EMPTY.copy()
    out = pd.DataFrame({"price": raw["Close"].astype(float), "volume": raw["Volume"].astype(float)})
    out.index = pd.DatetimeIndex(raw.index).tz_convert("UTC")
    out.index.name = "ts"
    return out.dropna(subset=["price"])


def yfinance_bars(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """1-minute bars from Yahoo in <=7-day chunks. Raises on a transport error like the venue clients."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    frames = []
    cur = pd.Timestamp(start).tz_convert("UTC")
    end = pd.Timestamp(end).tz_convert("UTC")
    while cur < end:
        chunk_end = min(end, cur + pd.Timedelta(days=7))
        raw = t.history(start=cur, end=chunk_end, interval="1m", auto_adjust=False, prepost=False)
        frames.append(shape_yfinance(raw))
        cur = chunk_end
    frames = [f for f in frames if not f.empty]
    if not frames:
        return EMPTY.copy()
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()
