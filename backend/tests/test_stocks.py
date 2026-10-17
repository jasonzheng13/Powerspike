"""On-demand stock and contract bars: freshness rules, pulls, company metadata. No network."""

import pandas as pd
import pytest

from powerspike import stocks
from powerspike.ingest import cache, equities, polymarket
from powerspike.synthetic import equity_sessions, quiet_market

SESSIONS = ["2025-12-09", "2025-12-10", "2025-12-11"]
AFTER_CLOSE = pd.Timestamp("2025-12-11 21:30", tz="UTC")     # Thu 16:30 ET
NEXT_MORNING = pd.Timestamp("2025-12-12 15:00", tz="UTC")    # Fri 10:00 ET, in session
DAY = pd.Timedelta(days=1)


@pytest.fixture
def con(tmp_path):
    return cache.connect(tmp_path / "c.duckdb")


@pytest.fixture
def yf(monkeypatch):
    """Stub `equities.backfill_bars`; records calls; serves 3 synthetic sessions clipped to [start, end]."""
    calls = []
    frame = equity_sessions(SESSIONS, p0=180.0, seed=1)

    def fake(ticker, start, end):
        calls.append((ticker, pd.Timestamp(start), pd.Timestamp(end)))
        if ticker == "ZZZZZZ":
            return equities.EMPTY.copy()
        return frame[(frame.index >= start) & (frame.index <= end)]

    monkeypatch.setattr(equities, "backfill_bars", fake)
    return calls


def test_stale_rules():
    last = pd.Timestamp("2025-12-11 20:59", tz="UTC")               # last bar of Thursday's session
    assert stocks.stale(None, AFTER_CLOSE)
    assert not stocks.stale(last, AFTER_CLOSE)                       # after the close, that close is enough
    assert stocks.stale(last, pd.Timestamp("2025-12-13 12:00", tz="UTC"))       # Saturday: Friday's session is missing
    fri = pd.Timestamp("2025-12-12 20:59", tz="UTC")
    assert not stocks.stale(fri, pd.Timestamp("2025-12-13 12:00", tz="UTC"))    # weekend, Friday cached
    assert stocks.stale(last, NEXT_MORNING)                          # in session, 18 h old
    assert not stocks.stale(NEXT_MORNING - pd.Timedelta(minutes=30), NEXT_MORNING)
    assert stocks.stale(NEXT_MORNING - pd.Timedelta(minutes=90), NEXT_MORNING)
    assert stocks.stale(pd.Timestamp("2025-12-10 20:59", tz="UTC"), AFTER_CLOSE)  # a day behind


def test_first_refresh_pulls_the_whole_range_and_a_fresh_one_does_not(con, yf):
    token = stocks.refresh(con, "nvda", days=3, now=AFTER_CLOSE)
    assert len(yf) == 1 and yf[0] == ("NVDA", AFTER_CLOSE - 3 * DAY, AFTER_CLOSE)
    assert token == pd.Timestamp("2025-12-11 20:59", tz="UTC")
    assert len(cache.load(con, "equity:NVDA")) == 3 * 390
    assert stocks.refresh(con, "NVDA", days=3, now=AFTER_CLOSE) == token
    assert len(yf) == 1


def test_stale_refresh_pulls_only_the_tail(con, yf):
    stocks.refresh(con, "NVDA", days=3, now=AFTER_CLOSE)
    stocks.refresh(con, "NVDA", days=3, now=NEXT_MORNING)
    assert len(yf) == 2
    _, start, end = yf[1]
    assert start == pd.Timestamp("2025-12-11 20:59", tz="UTC") - DAY and end == NEXT_MORNING


def test_unknown_ticker_raises(con, yf):
    with pytest.raises(stocks.UnknownTicker):
        stocks.refresh(con, "ZZZZZZ", days=3, now=AFTER_CLOSE)


def test_ensure_bars_returns_the_window(con, yf):
    df = stocks.ensure_bars(con, "NVDA", days=2, now=AFTER_CLOSE)
    assert list(df.columns) == ["price", "volume"] and len(df) == 2 * 390
    assert df.index[0] >= AFTER_CLOSE - 2 * DAY


def test_company_metadata_configured_and_not(monkeypatch):
    c = stocks.company("nvda")
    assert c["ticker"] == "NVDA" and c["configured"] is True and c["contracts"]
    monkeypatch.setattr(stocks, "company_name", lambda t: "Zed Corp")
    z = stocks.company("zzzq")
    assert z == {"ticker": "ZZZQ", "name": "Zed Corp", "sector": None, "news_query": '"Zed Corp" OR ZZZQ',
                 "contracts": [], "configured": False}


CONTRACT = {"id": "poly:test", "venue": "polymarket", "token_id": "123", "name": "t", "question": "q?", "event_sign": 1}


@pytest.fixture
def poly(monkeypatch):
    calls = []
    frame = quiet_market(n=3 * 1440, start="2025-12-09 00:00", seed=2)
    frame["volume"] = float("nan")

    def fake(token_id, start, end, fidelity=1):
        calls.append((token_id, pd.Timestamp(start), pd.Timestamp(end)))
        return frame[(frame.index >= start) & (frame.index <= end)]

    monkeypatch.setattr(polymarket, "price_history", fake)
    return calls


def test_ensure_contract_pulls_prices_only_and_is_a_noop_when_fresh(con, poly):
    now = pd.Timestamp("2025-12-11 23:30", tz="UTC")
    last = stocks.ensure_contract(con, CONTRACT, days=3, now=now)
    assert len(poly) == 1 and poly[0] == ("123", now - 3 * DAY, now)
    assert last == pd.Timestamp("2025-12-11 23:30", tz="UTC") and cache.last_ts(con, "poly:test") == last
    assert stocks.ensure_contract(con, CONTRACT, days=3, now=now + pd.Timedelta(minutes=20)) == last
    assert len(poly) == 1
    assert len(stocks.ensure_contract(con, CONTRACT, days=3, now=now + pd.Timedelta(hours=2)) and poly) == 2


def test_ensure_contract_returns_none_when_the_venue_has_nothing(con, monkeypatch):
    monkeypatch.setattr(polymarket, "price_history", lambda *a, **k: pd.DataFrame(columns=["price", "volume"]))
    assert stocks.ensure_contract(con, CONTRACT, days=3, now=AFTER_CLOSE) is None
