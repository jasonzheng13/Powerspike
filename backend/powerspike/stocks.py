"""On-demand bars for any stock ticker, plus the Polymarket contract configured for it.

The stock dashboard is search-driven, so nothing is pulled ahead of time: `refresh()` loads what
the cache has under `equity:<TICKER>`, pulls what is missing or stale from yfinance, and returns
the newest bar's timestamp. That timestamp is the *freshness token* the API keys its caches on, so
a re-pull shows up without a restart. Contracts work the same way under their `companies.yaml` id,
price only (`polymarket.price_history`): the lead/lag against a stock spike never needs the
contract's volume, and the trades feed would make a first load take a minute.
"""

from __future__ import annotations

import threading
from functools import lru_cache

import pandas as pd

from . import config
from .ingest import cache, equities, polymarket, to_grid
from .ingest.equities import equity_id, in_session, prev_close

DEFAULT_DAYS = 30
STALE_IN_SESSION = pd.Timedelta(hours=1)   # during the session, re-pull once the last bar is this old
CONTRACT_STALE = pd.Timedelta(hours=1)     # contracts trade around the clock
HEAD_WINDOW = pd.Timedelta(days=5)         # a window this long with no bars means the cache lacks the head
ONE_DAY = pd.Timedelta(days=1)

_heads_pulled: set[tuple[str, str]] = set()  # (series id, window start date) pulled successfully this process
# One lock per series: the frontend asks for bars and spikes at once, and two threads pulling the same
# ticker would both hit yfinance and then collide on the cache's primary key.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(series_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(series_id, threading.Lock())


class UnknownTicker(ValueError):
    """yfinance returned nothing for the ticker."""


def _now() -> pd.Timestamp:
    return pd.Timestamp.now(tz="UTC")


def stale(last_bar: pd.Timestamp | None, now: pd.Timestamp) -> bool:
    """Is the cached stock series behind what the market has produced by `now`?"""
    if last_bar is None:
        return True
    if in_session(now):
        return now - last_bar > STALE_IN_SESSION
    # Outside the session everything up to the last close should be there (bars are labelled by
    # their start minute, so the last one is 15:59 ET).
    return last_bar < prev_close(now) - pd.Timedelta(minutes=1)


def _head_key(series_id: str, start: pd.Timestamp) -> tuple[str, str]:
    return (series_id, str(pd.Timestamp(start).date()))


def _head_missing(con, series_id: str, start: pd.Timestamp) -> bool:
    """True when the window needs bars before what the cache holds and no pull for them has succeeded yet."""
    if _head_key(series_id, start) in _heads_pulled:
        return False
    return cache.load(con, series_id, start, start + HEAD_WINDOW).empty


def _pull(con, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> None:
    df = equities.session_grid(equities.backfill_bars(ticker, start, end))
    if not df.empty:
        cache.save(con, equity_id(ticker), df)


def refresh(con, ticker: str, days: int = DEFAULT_DAYS, now: pd.Timestamp | None = None) -> pd.Timestamp:
    """Bring `equity:<ticker>` up to date for the last `days` and return the newest bar's timestamp."""
    now = _now() if now is None else pd.Timestamp(now)
    ticker = ticker.upper()
    mid = equity_id(ticker)
    start = now - pd.Timedelta(days=days)
    with _lock(mid):
        last = cache.last_ts(con, mid)
        if last is None or _head_missing(con, mid, start):
            _pull(con, ticker, start, now)
            _heads_pulled.add(_head_key(mid, start))   # only after a pull that did not raise
        elif stale(last, now):
            _pull(con, ticker, last - ONE_DAY, now)   # the upsert overwrites the overlap
        last = cache.last_ts(con, mid)
    if last is None:
        raise UnknownTicker(ticker)
    return last


def ensure_bars(con, ticker: str, days: int = DEFAULT_DAYS, now: pd.Timestamp | None = None) -> pd.DataFrame:
    now = _now() if now is None else pd.Timestamp(now)
    refresh(con, ticker, days, now)
    return cache.load(con, equity_id(ticker), start=now - pd.Timedelta(days=days))


@lru_cache(maxsize=256)
def company_name(ticker: str) -> str:
    """Yahoo's short name for an unconfigured ticker; slow, so cached per process; the ticker on any error."""
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
        return info.get("shortName") or info.get("longName") or ticker
    except Exception:
        return ticker


def company(ticker: str) -> dict:
    """companies.yaml entry (`configured: True`) or a minimal one built from the ticker."""
    t = ticker.upper()
    c = config.company(t)
    if c:
        return {**c, "configured": True}
    name = company_name(t)
    return {"ticker": t, "name": name, "sector": None, "news_query": f'("{name}" OR {t})', "contracts": [], "configured": False}


def _pull_contract(con, contract: dict, start: pd.Timestamp, end: pd.Timestamp) -> None:
    df = to_grid(polymarket.price_history(contract["token_id"], start, end))
    if not df.empty:
        cache.save(con, contract["id"], df)


def ensure_contract(con, contract: dict, days: int = DEFAULT_DAYS, now: pd.Timestamp | None = None) -> pd.Timestamp | None:
    """Bring a contract's price grid up to date; returns its newest bar, or None when the venue has nothing."""
    now = _now() if now is None else pd.Timestamp(now)
    cid = contract["id"]
    start = now - pd.Timedelta(days=days)
    with _lock(cid):
        last = cache.last_ts(con, cid)
        if last is None or _head_missing(con, cid, start):
            _pull_contract(con, contract, start, now)
            _heads_pulled.add(_head_key(cid, start))
        elif now - last > CONTRACT_STALE:
            _pull_contract(con, contract, last - ONE_DAY, now)
        return cache.last_ts(con, cid)


def contract_frame(con, contract: dict, days: int = DEFAULT_DAYS, now: pd.Timestamp | None = None) -> pd.DataFrame:
    now = _now() if now is None else pd.Timestamp(now)
    ensure_contract(con, contract, days, now)
    return cache.load(con, contract["id"], start=now - pd.Timedelta(days=days))
