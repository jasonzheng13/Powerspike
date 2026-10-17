"""Equity (ETF) minute bars for the lead/lag question, in the same bar frame as the venues.

Same contract as polymarket.py / kalshi.py: a DataFrame indexed by UTC timestamp with
columns `price` and `volume`, or an empty frame when there is no data. Prices are dollars,
not probabilities, so `leadlag.py` works in log price rather than log-odds.

Stocks trade 09:30-16:00 America/New_York on weekdays, while prediction markets trade
around the clock. `session_grid` therefore applies `to_grid` *per session* so a price is
never forward-filled across the overnight or weekend gap, and `next_open` / `in_session`
let lead/lag say "measured from the next open" for an after-hours spike.

Historical source: yfinance. 1-minute bars exist only for the last ~30 days and 5-minute bars
for the last ~60 (max 7 days per request). `fetch_plan` splits a window into the stretch that
still has 1-minute bars and the older stretch that must be pulled at 5 minutes; `session_grid`
puts both on the same 1-minute grid (a 5-minute close is carried forward over the next four
minutes inside the session), so nothing downstream knows the difference beyond a reaction
timestamp being rounded to 5 minutes in the backfilled stretch. A live source (Alpaca) slots in
behind the same `shape_*` + `session_grid` path. Holidays are ignored: a market holiday simply
has no bars and no reaction is found.
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


def prev_close(ts: pd.Timestamp) -> pd.Timestamp:
    """16:00 ET of the most recent completed session at or before `ts` (the close itself counts)."""
    t = pd.Timestamp(ts).tz_convert(NY)
    day = t.normalize()
    if t.time() < CLOSE or t.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return (day + pd.Timedelta(hours=CLOSE.hour, minutes=CLOSE.minute)).tz_convert("UTC")


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


ONE_MIN_DAYS = 29   # Yahoo serves 1-minute bars for the last ~30 days
FIVE_MIN_DAYS = 59  # and 5-minute bars for the last ~60


def fetch_plan(start: pd.Timestamp, end: pd.Timestamp, now: pd.Timestamp | None = None
               ) -> list[tuple[pd.Timestamp, pd.Timestamp, str]]:
    """Split [start, end) into (start, end, interval) pieces Yahoo can still serve, oldest first.

    Older than ~60 days is dropped, 30-60 days is "5m", the rest "1m". Empty when nothing remains.
    """
    now = pd.Timestamp.now(tz="UTC") if now is None else now
    cut = now - pd.Timedelta(days=ONE_MIN_DAYS)
    floor = now - pd.Timedelta(days=FIVE_MIN_DAYS)
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    plan = []
    lo, hi = max(start, floor), min(end, cut)
    if lo < hi:
        plan.append((lo, hi, "5m"))
    lo, hi = max(start, cut), end
    if lo < hi:
        plan.append((lo, hi, "1m"))
    return plan


def _dedupe(frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if not f.empty]
    if not frames:
        return EMPTY.copy()
    df = pd.concat(frames)
    return df[~df.index.duplicated(keep="last")].sort_index()


def yfinance_bars(ticker: str, start: pd.Timestamp, end: pd.Timestamp, interval: str = "1m") -> pd.DataFrame:
    """Bars from Yahoo at `interval` in <=7-day chunks. Raises on a transport error like the venue clients."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    frames = []
    cur = pd.Timestamp(start).tz_convert("UTC")
    end = pd.Timestamp(end).tz_convert("UTC")
    while cur < end:
        chunk_end = min(end, cur + pd.Timedelta(days=7))
        raw = t.history(start=cur, end=chunk_end, interval=interval, auto_adjust=False, prepost=False)
        frames.append(shape_yfinance(raw))
        cur = chunk_end
    return _dedupe(frames)


def backfill_bars(ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """`yfinance_bars` over every piece of `fetch_plan`, joined; raw bars, not yet on the grid."""
    return _dedupe([yfinance_bars(ticker, s, e, interval=iv) for s, e, iv in fetch_plan(start, end)])
