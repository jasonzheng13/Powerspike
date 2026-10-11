import numpy as np
import pandas as pd
import pytest

from powerspike.detect import DetectorParams, detect, detect_drifts, detect_jumps, logit, robust_z
from powerspike.synthetic import add_drift, add_fat_finger, add_jump, demo_market, quiet_market


def test_logit_symmetry():
    p = pd.Series([0.1, 0.5, 0.9])
    lg = logit(p)
    assert abs(lg.iloc[1]) < 1e-12
    assert np.isclose(lg.iloc[0], -lg.iloc[2])


def test_robust_z_ignores_earlier_outlier():
    x = pd.Series(np.r_[np.zeros(100), 50.0, np.zeros(100), 1.0])
    z = robust_z(x, baseline=150, min_scale=0.1)
    # one huge earlier value must not shrink the z of the later move
    assert z.iloc[-1] >= 9


def test_quiet_market_has_no_jumps():
    for seed in range(5):
        assert detect_jumps(quiet_market(seed=seed)) == []


def test_real_jump_fires_near_injection():
    df = add_jump(quiet_market(seed=1), at=700, size=0.9)
    spikes = detect_jumps(df, "m")
    assert len(spikes) == 1
    s = spikes[0]
    assert s.direction == "up"
    assert abs((s.peak - df.index[700]).total_seconds()) <= 30 * 60
    assert s.volume_ratio > 2


def test_fat_finger_does_not_fire():
    df = add_fat_finger(quiet_market(seed=2), at=500, size=1.5)
    assert detect_jumps(df) == []


def test_jump_without_volume_is_damped():
    base = quiet_market(seed=3)
    loud = add_jump(base, at=700, size=0.9, vol_mult=6.0)
    thin = add_jump(base, at=700, size=0.9, vol_mult=1.0)
    p = DetectorParams()
    assert detect_jumps(loud, params=p)
    assert max((s.score for s in detect_jumps(thin, params=p)), default=0) < max(s.score for s in detect_jumps(loud, params=p))


def _no_trades_except(df, bar, shares):
    """A market where nobody normally trades: zero volume except `shares` at one bar."""
    out = df.assign(volume=0.0)
    out.iloc[bar, out.columns.get_loc("volume")] = shares
    return out


def test_thin_market_tiny_trade_does_not_confirm_a_jump():
    jumped = add_jump(quiet_market(seed=1), at=700, size=0.9)
    assert detect_jumps(_no_trades_except(jumped, 700, shares=5)) == []


def test_thin_market_real_trade_still_confirms_a_jump():
    jumped = add_jump(quiet_market(seed=1), at=700, size=0.9)
    spikes = detect_jumps(_no_trades_except(jumped, 700, shares=1000))
    assert len(spikes) == 1
    # The floor is `min_notional` dollars at risk, converted to shares at the peak bar's price, not ~0.
    p = float(jumped["price"].loc[spikes[0].peak])
    assert spikes[0].volume_ratio == pytest.approx(1000 * min(p, 1 - p) / DetectorParams().min_notional)


def test_thin_market_small_dollar_trade_does_not_confirm_a_jump():
    # 200 shares sounds like volume, but at ~40c it is ~$80: under the dollar floor, so no credit.
    jumped = add_jump(quiet_market(seed=1), at=700, size=0.9)
    assert detect_jumps(_no_trades_except(jumped, 700, shares=200)) == []


def _penny_market(n: int = 1440, lo: float = 0.01, hi: float = 0.02) -> pd.DataFrame:
    """A contract pinned at 1-2c that ticks up at bar 700 on heavy volume and stays there."""
    idx = pd.date_range("2025-12-10", periods=n, freq="1min", tz="UTC")
    price = np.where(np.arange(n) < 700, lo, hi)
    vol = np.full(n, 50.0)
    vol[700:715] = 20000.0
    return pd.DataFrame({"price": price, "volume": vol}, index=idx)


def test_one_tick_in_the_tail_is_not_a_jump():
    # 1c -> 2c is huge in log-odds but it is one tick of noise; neither detector may fire.
    df = _penny_market()
    assert detect_jumps(df) == []
    assert detect_drifts(df) == []
    assert detect_jumps(_penny_market(lo=0.99, hi=0.98)) == []


def test_move_out_of_the_tail_still_fires():
    # 3% -> 19% has one end inside the scored range, so it is a real move.
    df = add_jump(quiet_market(seed=1, p0=0.03), at=700, size=2.0)
    assert detect_jumps(df, "m")


def test_slow_drift_caught_by_cusum_not_jump():
    df = add_drift(quiet_market(seed=4), at=600, size=-0.8, over=300)
    assert detect_jumps(df) == []
    drifts = detect_drifts(df)
    assert drifts and drifts[0].direction == "down"
    assert df.index[600] <= drifts[0].peak <= df.index[1000]


def test_drift_on_an_empty_market_does_not_fire():
    df = add_drift(quiet_market(seed=4), at=600, size=-0.8, over=300).assign(volume=0.0)
    assert detect_drifts(df) == []


def test_drift_on_normal_volume_fires_with_its_volume_ratio():
    df = add_drift(quiet_market(seed=4), at=600, size=-0.8, over=300)
    drifts = detect_drifts(df)
    assert drifts and drifts[0].volume_ratio > 0.8  # normal trading, not a surge, is enough for a drift


def test_drift_without_any_volume_data_still_fires():
    df = add_drift(quiet_market(seed=4), at=600, size=-0.8, over=300).assign(volume=float("nan"))
    drifts = detect_drifts(df)
    assert drifts and np.isnan(drifts[0].volume_ratio)


def test_drift_card_starts_before_the_first_move():
    # Flat at 30%, then one step to 50%: the CUSUM alarm fires on the step's own 15-min bar.
    idx = pd.date_range("2025-12-10", periods=1440, freq="1min", tz="UTC")
    df = pd.DataFrame({"price": np.where(np.arange(1440) < 1000, 0.3, 0.5), "volume": np.nan}, index=idx)
    (d,) = detect_drifts(df)
    assert d.start < d.peak
    assert (d.p_before, d.p_after) == (0.3, 0.5)
    assert d.headline().startswith("30% → 50% in 15 min")


def test_demo_market_end_to_end():
    spikes = detect(demo_market(), "demo")
    kinds = [s.kind for s in spikes]
    assert "jump" in kinds and "drift" in kinds
    # nothing fires on the fat finger around bar 420
    ff = demo_market().index[420]
    assert not any(abs((s.peak - ff).total_seconds()) < 45 * 60 for s in spikes)
