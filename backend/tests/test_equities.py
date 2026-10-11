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
