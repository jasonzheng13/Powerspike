"""Lead/lag between a market spike and an ETF, on synthetic data only.

Timing is onset vs onset: the ETF's first detectable bar against `spike.start`, in trading minutes.
"""

import numpy as np
import pandas as pd
import pytest

from powerspike import leadlag
from powerspike.detect import Spike, detect
from powerspike.ingest import equities
from powerspike.synthetic import add_equity_move, add_jump, demo_equity, demo_market, equity_sessions, quiet_market

SESSIONS = ["2025-12-09", "2025-12-10", "2025-12-11"]
MIN = pd.Timedelta(minutes=1)


def _spike(peak: pd.Timestamp, window: int = 15) -> Spike:
    return Spike("m", "jump", peak - window * MIN, peak, peak + 30 * MIN, 0.4, 0.6, 5.0, 3.0, 1.0, 10.0)


@pytest.fixture
def jump():
    # Start at 03:00 UTC so bar 700 lands at 14:40 UTC = 09:40 EST, inside the NYSE session.
    df = add_jump(quiet_market(n=1440, p0=0.38, noise=0.006, seed=7, start="2025-12-10 03:00"), at=700, size=0.95, over=12)
    s = [s for s in detect(df, "m") if s.kind == "jump"][0]
    assert equities.in_session(s.start) and equities.in_session(s.peak)
    return s


def _etf(move_at: pd.Timestamp | None, size: float = 0.01, days=SESSIONS) -> pd.DataFrame:
    df = equity_sessions(days, p0=100.0, noise=0.0003, seed=1)
    return add_equity_move(df, at=move_at, size=size) if move_at is not None else df


def _gap(df: pd.DataFrame, day: str, size: float) -> pd.DataFrame:
    """Open session `day` with a gap of `size` log units and keep it."""
    out = df.copy()
    at = pd.Timestamp(f"{day} 09:30", tz="America/New_York")
    out.loc[out.index >= at, "price"] = np.round(out.loc[out.index >= at, "price"] * np.exp(size), 2)
    return out


# ---- timing ------------------------------------------------------------------------------

def test_equity_sessions_are_session_only_bar_frames():
    df = equity_sessions(SESSIONS[:2], p0=100.0, noise=0.0003, seed=1)
    assert list(df.columns) == ["price", "volume"] and len(df) == 2 * 390
    assert all(equities.in_session(t) for t in df.index[::50])


def test_etf_moving_2_minutes_after_the_market_onset_is_market_led(jump):
    r = leadlag.react(jump, _etf(jump.start + 2 * MIN), "TLT", expected_move=1)
    assert r.status == "reacted" and 2 <= r.lag_min <= 6
    assert r.consistent is True and r.ret > 0 and r.z >= leadlag.LeadLagParams().sig_z
    assert r.after_hours is False and r.spans_close is False


def test_etf_moving_10_minutes_after_the_onset_gives_lag_near_10(jump):
    r = leadlag.react(jump, _etf(jump.start + 10 * MIN), "TLT", expected_move=1)
    assert 10 <= r.lag_min <= 14


def test_etf_moving_before_the_market_gives_negative_lag():
    s = _spike(pd.Timestamp("2025-12-10 15:30", tz="UTC"))     # onset 15:15 UTC = 10:15 EST
    r = leadlag.react(s, _etf(s.start - 20 * MIN), "TLT", expected_move=1)
    assert r.status == "reacted" and -20 <= r.lag_min <= -14


def test_flat_etf_is_quiet_not_no_data(jump):
    r = leadlag.react(jump, _etf(None), "TLT", expected_move=1)
    assert r.status == "quiet" and r.lag_min is None and r.etf_peak is None and r.consistent is None


def test_magnitude_and_direction_come_from_the_first_reacting_run(jump):
    # Dips 1% at +3 min, then rallies 3% at +40 min: dated at +3 and labelled by the dip.
    etf = add_equity_move(_etf(jump.start + 3 * MIN, size=-0.01), at=jump.start + 40 * MIN, size=0.03)
    r = leadlag.react(etf=etf, spike=jump, ticker="TLT", expected_move=1)
    assert 3 <= r.lag_min <= 7 and r.ret < 0 and r.consistent is False


def test_direction_check_uses_expected_move(jump):
    etf = _etf(jump.start + 5 * MIN, size=-0.01)  # ETF falls
    assert leadlag.react(jump, etf, "KRE", expected_move=-1).consistent is True
    assert leadlag.react(jump, etf, "TLT", expected_move=1).consistent is False
    assert leadlag.react(jump, etf, "BIL", expected_move=0).consistent is None


def test_confirmed_at_waits_for_the_search_window(jump):
    p = leadlag.LeadLagParams()
    r = leadlag.react(jump, _etf(None), "TLT", expected_move=1, params=p)
    assert r.confirmed_at >= jump.start + p.lookaround * MIN
    assert r.confirmed_at >= jump.confirmed_at


# ---- session edges -----------------------------------------------------------------------

def test_opening_gap_before_an_early_spike_is_the_etf_leading_not_a_reaction_at_plus_zero():
    # Market moves at 09:40 ET; the ETF gapped 2% at 09:30 and is flat after: it led by 10 trading minutes.
    s = _spike(pd.Timestamp("2025-12-10 14:55", tz="UTC"))
    r = leadlag.react(s, _gap(_etf(None), "2025-12-10", 0.02), "TLT", expected_move=1)
    assert r.status == "reacted" and r.lag_min == -10 and r.ret > 0.019


def test_first_window_bars_of_a_session_carry_no_intraday_return():
    # The overnight gap must not leak into the 15-bar returns at 09:30-09:44 (that is what `gap` is for).
    z = leadlag.equity_z(_gap(_etf(None), "2025-12-10", 0.02))
    day = z[z.index.tz_convert("America/New_York").date == pd.Timestamp("2025-12-10").date()]
    assert day["delta"].iloc[:15].isna().all() and day["delta"].iloc[15:].notna().all()
    assert day["gap"].iloc[0] > 0.019 and day["gap"].iloc[1:].isna().all()


def test_after_hours_spike_reacts_at_the_open_via_the_gap():
    s = _spike(pd.Timestamp("2025-12-10 03:00", tz="UTC"))      # 22:00 ET the night before
    r = leadlag.react(s, _gap(_etf(None), "2025-12-10", 0.01), "TLT", expected_move=1)
    assert r.after_hours is True and r.status == "reacted" and r.lag_min == 0
    assert r.ret > 0.009 and r.consistent is True
    open_ = equities.next_open(s.start)
    assert r.confirmed_at >= open_ + leadlag.LeadLagParams().lookaround * MIN


def test_after_hours_spike_with_a_flat_open_can_still_react_intraday():
    s = _spike(pd.Timestamp("2025-12-10 03:00", tz="UTC"))
    open_ = equities.next_open(s.start)
    r = leadlag.react(s, _etf(open_ + 25 * MIN), "TLT", expected_move=1)
    assert r.after_hours is True and r.status == "reacted" and 25 <= r.lag_min <= 29


def test_spike_before_the_close_carries_the_window_into_the_next_session():
    # Onset 15:50 ET: 10 trading minutes left today, the rest of the hour is tomorrow morning.
    s = _spike(pd.Timestamp("2025-12-10 21:05", tz="UTC"))      # start = 20:50 UTC = 15:50 EST
    assert equities.in_session(s.start)
    nxt = equities.next_open(pd.Timestamp("2025-12-10 21:30", tz="UTC"))
    r = leadlag.react(s, _etf(nxt + 5 * MIN), "TLT", expected_move=1)
    assert r.status == "reacted" and r.spans_close is True and r.after_hours is False
    # 10 bars today, then a move at 09:35 is first visible at 09:45 (bar 15, once a 15-bar return exists).
    assert 24 <= r.lag_min <= 28
    assert r.confirmed_at.tz_convert("America/New_York").date() == pd.Timestamp("2025-12-11").date()


def test_window_without_bars_is_no_data_not_quiet(jump):
    assert leadlag.react(jump, pd.DataFrame(columns=["price", "volume"]), "TLT", expected_move=1).status == "no_data"
    ends_early = _etf(None, days=SESSIONS[:1])                 # bars end the day before the spike
    assert leadlag.react(jump, ends_early, "TLT", expected_move=1).status == "no_data"


# ---- verdicts ----------------------------------------------------------------------------

def test_lead_lag_verdict_and_summary(jump):
    eq = {"TLT": _etf(jump.start + 4 * MIN), "KRE": _etf(jump.start + 8 * MIN, size=-0.01), "XLF": _etf(None)}
    ll = leadlag.lead_lag(jump, eq, expected={"TLT": 1, "KRE": -1, "XLF": -1}, event_sign=1)
    assert ll.verdict == "market led" and 4 <= ll.median_lag <= 10
    assert [r.ticker for r in ll.reactions] == ["TLT", "KRE", "XLF"]
    assert ll.confirmed_at == max(r.confirmed_at for r in ll.reactions)
    s = leadlag.summary([ll, ll])
    assert s["spikes"] == 2 and s["led"] == 2 and s["no_move"] == 0 and s["no_data"] == 0
    assert s["consistent_share"] == 1.0 and 4 <= s["median_lag_min"] <= 10


def test_a_single_reacting_etf_is_not_enough_for_a_verdict(jump):
    eq = {"TLT": _etf(jump.start + 4 * MIN), "KRE": _etf(None), "XLF": _etf(None)}
    ll = leadlag.lead_lag(jump, eq, expected={"TLT": 1, "KRE": -1, "XLF": -1})
    assert ll.verdict == "no equity move" and ll.reactions[0].status == "reacted"
    assert leadlag.lead_lag(jump, {"TLT": eq["TLT"]}, expected={"TLT": 1}).verdict == "market led"  # 1 of 1 configured


def test_after_hours_spike_gets_its_own_verdict_bucket():
    s = _spike(pd.Timestamp("2025-12-10 03:00", tz="UTC"))
    gapped = _gap(_etf(None), "2025-12-10", 0.01)
    ll = leadlag.lead_lag(s, {"TLT": gapped, "IEF": gapped, "XLF": _etf(None)}, expected={"TLT": 1, "IEF": 1, "XLF": -1})
    assert ll.verdict == "market led (overnight)" and ll.median_lag == 0
    assert leadlag.summary([ll])["led_overnight"] == 1 and leadlag.summary([ll])["led"] == 0


def test_lead_lag_event_sign_flips_the_expected_move(jump):
    eq = {"TLT": _etf(jump.start + 4 * MIN, size=-0.01)}
    assert leadlag.lead_lag(jump, eq, expected={"TLT": 1}, event_sign=-1).reactions[0].consistent is True


def test_no_equity_data_gives_no_data_verdict_and_is_excluded_from_summary(jump):
    ll = leadlag.lead_lag(jump, {"TLT": pd.DataFrame(columns=["price", "volume"])}, expected={"TLT": 1})
    assert ll.verdict == "no data" and ll.reactions[0].status == "no_data"
    s = leadlag.summary([ll])
    assert s["no_data"] == 1 and s["no_move"] == 0 and s["median_lag_min"] is None


def test_to_json_round_trips_timestamps_and_nulls(jump):
    d = leadlag.to_json(leadlag.lead_lag(jump, {"TLT": _etf(None)}, expected={"TLT": 1}))
    assert d["confirmed_at"].endswith("+00:00") and d["reactions"][0]["lag_min"] is None
    assert d["reactions"][0]["status"] == "quiet" and d["verdict"] == "no equity move" and d["market_id"] == "m"


def test_demo_market_jump_is_in_session_and_demo_equity_reacts():
    s = [s for s in detect(demo_market(), "synthetic-demo") if s.kind == "jump"][0]
    assert equities.in_session(s.start)
    tlt = demo_equity("TLT")
    assert all(equities.in_session(t) for t in tlt.index[::50])
    r = leadlag.react(s, tlt, "TLT", expected_move=1)
    assert r.status == "reacted" and r.lag_min > 0


def test_run_cached_scores_every_cached_market_against_cached_etfs(tmp_path):
    from powerspike.ingest import cache

    con = cache.connect(tmp_path / "c.duckdb")
    cache.save(con, "demo", demo_market())
    cache.save(con, equities.equity_id("TLT"), demo_equity("TLT"))
    cache.save(con, equities.equity_id("KRE"), demo_equity("KRE"))
    rows = leadlag.run_cached(con, [{"id": "demo", "event_type": "fed_rates"}])
    assert [r.market_id for r in rows] == ["demo", "demo"]          # the jump and the drift
    tickers = {x.ticker for r in rows for x in r.reactions}
    assert tickers == {"TLT", "IEF", "HYG", "LQD", "KRE", "XLF"}  # uncached ETFs still appear, as no_data
    jump = [r for r in rows if r.kind == "jump"][0]
    assert jump.verdict == "market led"
    assert leadlag.run_cached(con, [{"id": "missing", "event_type": "fed_rates"}]) == []


# ---- stock spike vs. the company's 24/7 contract ------------------------------------------

ONSET = pd.Timestamp("2025-12-10 15:15", tz="UTC")   # 10:15 ET, in session
C0 = pd.Timestamp("2025-12-09 00:00", tz="UTC")


def _stock_spike(direction: str = "up") -> Spike:
    before, after = (180.0, 186.0) if direction == "up" else (186.0, 180.0)
    return Spike("equity:NVDA", "jump", ONSET, ONSET + 8 * MIN, ONSET + 38 * MIN, before, after, 6.0, 4.0, 1.0, 20.0, "usd")


def _contract(jump_at: pd.Timestamp | None, size: float = 0.6) -> pd.DataFrame:
    df = quiet_market(n=3 * 1440, p0=0.5, noise=0.006, seed=2, start="2025-12-09 00:00")
    df["volume"] = float("nan")   # price_history carries no volume
    if jump_at is None:
        return df
    return add_jump(df, at=int((jump_at - C0) / MIN), size=size, over=5)


def test_contract_moving_before_the_stock_is_contract_led():
    r = leadlag.react_contract(_stock_spike(), _contract(ONSET - 23 * MIN), "poly:t", event_sign=1)
    assert r.status == "reacted" and r.verdict == "contract led"
    assert -25 <= r.lag_min <= -19 and r.ret > 0 and r.consistent is True
    assert r.z >= leadlag.LeadLagParams().sig_z
    assert r.confirmed_at >= ONSET + leadlag.LeadLagParams().lookaround * MIN


def test_contract_moving_after_the_stock_is_stock_led():
    r = leadlag.react_contract(_stock_spike(), _contract(ONSET + 10 * MIN), "poly:t", event_sign=1)
    assert r.verdict == "stock led" and 10 <= r.lag_min <= 14


def test_contract_moving_with_the_stock_is_concurrent():
    r = leadlag.react_contract(_stock_spike(), _contract(ONSET - 1 * MIN), "poly:t", event_sign=1)
    assert r.verdict == "concurrent" and abs(r.lag_min) <= 2


def test_flat_contract_is_quiet():
    r = leadlag.react_contract(_stock_spike(), _contract(None), "poly:t", event_sign=1)
    assert r.status == "quiet" and r.verdict == "contract quiet" and r.lag_min is None and r.consistent is None


def test_missing_contract_bars_are_no_data():
    empty = pd.DataFrame(columns=["price", "volume"])
    assert leadlag.react_contract(_stock_spike(), empty, "poly:t", 1).verdict == "no data"
    ends_early = quiet_market(n=1440, p0=0.5, start="2025-12-08 00:00")      # ends the day before
    assert leadlag.react_contract(_stock_spike(), ends_early, "poly:t", 1).verdict == "no data"


def test_contract_event_sign_flips_consistency():
    down = _contract(ONSET - 10 * MIN, size=-0.6)   # odds fall while the stock rises
    assert leadlag.react_contract(_stock_spike(), down, "poly:t", event_sign=1).consistent is False
    assert leadlag.react_contract(_stock_spike(), down, "poly:t", event_sign=-1).consistent is True
    assert leadlag.react_contract(_stock_spike("down"), down, "poly:t", event_sign=1).consistent is True


def test_a_pinned_contract_ticking_one_cent_is_quiet():
    # 70% with 1c ticks: in log-return units a tick would score z≈50; the logit floor keeps it quiet.
    df = _contract(None)
    df["price"] = 0.70
    ticks = (df.index >= ONSET - 5 * MIN) & (df.index <= ONSET + 20 * MIN)
    df.loc[ticks, "price"] = np.where(np.arange(ticks.sum()) % 2 == 0, 0.71, 0.70)
    assert leadlag.react_contract(_stock_spike(), df, "poly:t", 1).verdict == "contract quiet"


def test_contract_to_json_round_trips_nulls_and_iso_timestamps():
    quiet = leadlag.contract_to_json(leadlag.react_contract(_stock_spike(), _contract(None), "poly:t", 1))
    assert quiet["lag_min"] is None and quiet["reaction_at"] is None and quiet["confirmed_at"].endswith("+00:00")
    led = leadlag.contract_to_json(leadlag.react_contract(_stock_spike(), _contract(ONSET - 23 * MIN), "poly:t", 1))
    assert isinstance(led["lag_min"], float) and led["reaction_at"].endswith("+00:00") and led["verdict"] == "contract led"
