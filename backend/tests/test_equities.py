"""Equity bars: NYSE session helpers and the per-session grid. No network."""

import pandas as pd

from powerspike.ingest import equities


def test_next_open_from_friday_evening_is_monday_open():
    # 2025-12-12 is a Friday; EST, so 09:30 ET = 14:30 UTC.
    fri = pd.Timestamp("2025-12-12 22:00", tz="UTC")
    assert equities.next_open(fri) == pd.Timestamp("2025-12-15 14:30", tz="UTC")


def test_next_open_overnight_is_same_day_open():
    assert equities.next_open(pd.Timestamp("2025-12-10 03:00", tz="UTC")) == pd.Timestamp("2025-12-10 14:30", tz="UTC")


def test_next_open_in_session_is_itself():
    t = pd.Timestamp("2025-12-10 15:07", tz="UTC")
    assert equities.in_session(t)
    assert equities.next_open(t) == t


def test_in_session_false_outside_hours_and_on_weekends():
    assert not equities.in_session(pd.Timestamp("2025-12-10 14:29", tz="UTC"))
    assert not equities.in_session(pd.Timestamp("2025-12-10 21:00", tz="UTC"))  # 16:00 EST
    assert not equities.in_session(pd.Timestamp("2025-12-13 15:00", tz="UTC"))  # Saturday


def _raw_session(day: str) -> pd.DataFrame:
    idx = pd.date_range(f"{day} 14:30", f"{day} 19:59", freq="1min", tz="UTC")
    df = pd.DataFrame({"price": 100.0, "volume": 10.0}, index=idx)
    return df.drop(idx[5:8])  # a 3-minute hole inside the session


def test_session_grid_fills_inside_a_session_but_not_across_the_night():
    raw = pd.concat([_raw_session("2025-12-09"), _raw_session("2025-12-10")])
    g = equities.session_grid(raw)
    assert len(g) == 2 * 330                         # the hole is filled
    assert g.loc["2025-12-09 14:36", "price"] == 100.0
    assert g.loc["2025-12-09 14:36", "volume"] == 0.0
    night = g[(g.index > "2025-12-09 19:59") & (g.index < "2025-12-10 14:30")]
    assert night.empty


def test_session_grid_empty_passthrough():
    assert equities.session_grid(pd.DataFrame(columns=["price", "volume"])).empty


def test_equity_id():
    assert equities.equity_id("tlt") == "equity:TLT"


def test_shape_yfinance_frame_to_bar_frame():
    idx = pd.date_range("2025-12-10 09:30", periods=3, freq="1min", tz="America/New_York")
    raw = pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": [99.0, 99.5, 99.7], "Volume": [10, 0, 5]}, index=idx)
    df = equities.shape_yfinance(raw)
    assert list(df.columns) == ["price", "volume"]
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp("2025-12-10 14:30", tz="UTC")
    assert df["price"].tolist() == [99.0, 99.5, 99.7]


def test_shape_yfinance_empty():
    assert equities.shape_yfinance(pd.DataFrame()).empty


def test_session_grid_spreads_five_minute_rows_onto_minutes_within_a_session():
    # Yahoo's 5-minute bars (older than ~30 days) must land on the same 1-minute grid
    # the lead/lag module reads: filled forward inside a session, never across the night.
    idx = pd.DatetimeIndex(
        ["2025-12-10 14:30", "2025-12-10 14:35", "2025-12-11 14:30"], tz="UTC"
    )
    raw = pd.DataFrame({"price": [100.0, 101.0, 90.0], "volume": [5.0, 7.0, 3.0]}, index=idx)
    g = equities.session_grid(raw)
    assert g.loc["2025-12-10 14:33", "price"] == 100.0       # filled from the 14:30 bar
    assert g.loc["2025-12-10 14:35", "price"] == 101.0
    assert g.loc["2025-12-11 14:30", "price"] == 90.0        # next day's open is its own bar
    assert g.index.max() == pd.Timestamp("2025-12-11 14:30", tz="UTC")
    assert g.loc["2025-12-10 14:33", "volume"] == 0.0        # volume is not spread


def test_fetch_plan_uses_five_minute_bars_only_for_the_stretch_yahoo_no_longer_serves_at_one_minute():
    now = pd.Timestamp("2026-10-11", tz="UTC")
    recent = now - pd.Timedelta(days=10)
    old = now - pd.Timedelta(days=45)
    too_old = now - pd.Timedelta(days=90)
    cut = now - pd.Timedelta(days=29)
    floor = now - pd.Timedelta(days=59)
    assert equities.fetch_plan(recent, now, now=now) == [(recent, now, "1m")]
    assert equities.fetch_plan(old, now, now=now) == [(old, cut, "5m"), (cut, now, "1m")]
    assert equities.fetch_plan(old, old + pd.Timedelta(days=3), now=now) == [(old, old + pd.Timedelta(days=3), "5m")]
    assert equities.fetch_plan(too_old, now, now=now) == [(floor, cut, "5m"), (cut, now, "1m")]
    assert equities.fetch_plan(too_old, too_old + pd.Timedelta(days=3), now=now) == []


def test_prev_close_is_the_last_completed_session_close():
    fri_close = pd.Timestamp("2025-12-12 21:00", tz="UTC")           # Friday 16:00 EST
    assert equities.prev_close(pd.Timestamp("2025-12-13 12:00", tz="UTC")) == fri_close   # Saturday
    assert equities.prev_close(pd.Timestamp("2025-12-15 15:00", tz="UTC")) == fri_close   # Monday mid-session
    mon_close = pd.Timestamp("2025-12-15 21:00", tz="UTC")
    assert equities.prev_close(pd.Timestamp("2025-12-16 01:00", tz="UTC")) == mon_close   # Monday 20:00 ET
    assert equities.prev_close(mon_close) == mon_close                                     # at the close itself
