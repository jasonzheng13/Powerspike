"""Spike detector: "a spike is a move that is big, backed by real money, and sticks."

Input is one market's series on a regular time grid (default 1 minute):
    DataFrame indexed by UTC timestamp with columns
        price   probability in [0, 1]
        volume  contracts / USD traded in that bar (NaN if the venue has none)

Two alert types come out:
    jump   a large, volume-confirmed, persistent move over `window` bars
    drift  a slow grind caught by a two-sided CUSUM on 1-bar log-odds changes

Every step uses only data up to the bar being scored, except the
persistence check, which by design looks `hold` bars ahead. In the live /
replay setting that means a jump alert is *confirmed* `hold` bars after
the move; `Spike.confirmed_at` records that time so the demo stays honest.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd

MAD_TO_SIGMA = 1.4826  # makes MAD comparable to a standard deviation for normal data
NY = "America/New_York"


@dataclass
class DetectorParams:
    window: int = 15            # bars over which a jump is measured (Δ logit over 15 min)
    baseline: int = 240         # bars of history for the rolling median / MAD (4 h)
    min_scale: float = 0.05     # floor on the robust scale of window changes, in logit units; stops flat markets from producing huge z
    vol_min_ratio: float = 2.0  # window volume must be >= this many times its usual level for full credit
    min_notional: float = 100.0 # floor on a window's usual volume, in dollars at risk (shares x min(p, 1-p)); stops thin markets from crediting a $5 trade
    tail: float = 0.05          # a move whose both ends lie under `tail` or over 1-`tail` is tick noise, not a repricing, and is not scored
    hold: int = 30              # bars after the move at which we check it stuck
    score_threshold: float = 4.0
    cooldown: int = 60          # bars after an alert during which the same market can't fire again
    smooth: int = 5             # bars in the rolling-median level; kills 1-2 bar fat-finger prints
    drift_bar: int = 15         # CUSUM runs on changes over non-overlapping bars of this many minutes
    drift_baseline: int = 96    # drift bars of history for the CUSUM scale (24 h of 15-min bars)
    cusum_k: float = 0.5        # CUSUM slack, in robust-sigma units per drift bar
    cusum_h: float = 6.0        # CUSUM alarm level, in robust-sigma units
    eps: float = 0.005          # clip prices to [eps, 1-eps] before logit
    onset_frac: float = 0.2     # a jump's `start` is the bar before the price had covered this share of the first hot window's change
    space: Literal["logit", "log"] = "logit"  # "log": price is dollars; moves are log returns, min_scale is in
                                # log-return units, min_notional converts to shares as dollars / price, and the
                                # tail gate is off (there is no settled end of a dollar price)


# Stocks: dollar prices on session-only 1-minute bars. Every shift in `score_series` is positional,
# so the overnight gap is simply one more bar: a move in the first bars of a session has its `start`
# at the previous close and `p_before` is roughly that close, and a move at 15:50 ET is confirmed the
# next morning. That is deliberate: the gap *is* the move the overnight news caused.
EQUITY_PARAMS = DetectorParams(space="log", min_scale=0.002, min_notional=1e5)


@dataclass
class Spike:
    market_id: str
    kind: str                  # "jump" | "drift"
    start: pd.Timestamp        # start of the move
    peak: pd.Timestamp         # bar where the score peaked (jump) or the alarm fired (drift)
    confirmed_at: pd.Timestamp # earliest time the alert could be shown live
    p_before: float
    p_after: float
    z: float                   # robust z of the window change (jump) or CUSUM level (drift)
    volume_ratio: float        # window volume / its usual level (NaN if no volume)
    persistence: float         # share of the move still held `hold` bars later, 0..1
    score: float
    unit: str = "prob"         # "prob" (0..1 odds) | "usd" (dollar price); last, with a default, so positional construction keeps working

    @property
    def direction(self) -> str:
        return "up" if self.p_after >= self.p_before else "down"

    def headline(self) -> str:
        """One-line summary for an alert card, e.g. 'Fed cut odds 38% → 61% in 15 min (6.2σ, 4.0× volume)'."""
        vol = "" if np.isnan(self.volume_ratio) else f", {self.volume_ratio:.1f}× volume"
        if self.unit == "usd":
            before, after = f"${self.p_before:,.2f}", f"${self.p_after:,.2f}"
        else:
            before, after = f"{self.p_before:.0%}", f"{self.p_after:.0%}"
        if self.start.tz_convert(NY).date() != self.peak.tz_convert(NY).date():
            when = "across the open"  # session-only bars: the move spans the overnight gap
        else:
            when = f"in {max(1, int((self.peak - self.start).total_seconds() // 60))} min"
        return f"{before} → {after} {when} ({self.z:.1f}σ{vol})"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["direction"] = self.direction
        return d


def logit(p: pd.Series, eps: float = 0.005) -> pd.Series:
    p = p.clip(eps, 1 - eps)
    return np.log(p / (1 - p))


def transform(price: pd.Series, p: DetectorParams) -> pd.Series:
    """The space moves are measured in: log-odds for a probability, log price for dollars."""
    return logit(price, p.eps) if p.space == "logit" else np.log(price.astype(float))


def _rolling_mad(x: pd.Series, n: int) -> pd.Series:
    """Exact rolling median absolute deviation."""
    def mad(a: np.ndarray) -> float:
        a = a[~np.isnan(a)]
        if a.size == 0:
            return np.nan
        return float(np.median(np.abs(a - np.median(a))))
    return x.rolling(n, min_periods=n // 2).apply(mad, raw=True)


def robust_scale(x: pd.Series, baseline: int, min_scale: float, lag: int = 1) -> pd.Series:
    """1.4826 × rolling MAD of `x`, floored at `min_scale`, using only history that ends `lag` bars ago."""
    return (MAD_TO_SIGMA * _rolling_mad(x.shift(lag), baseline)).clip(lower=min_scale)


def robust_z(x: pd.Series, baseline: int, min_scale: float, lag: int = 1) -> pd.Series:
    """(x - rolling median) / (1.4826 × rolling MAD), using only history that ends `lag` bars ago."""
    hist = x.shift(lag)
    med = hist.rolling(baseline, min_periods=baseline // 2).median()
    return (x - med) / robust_scale(x, baseline, min_scale, lag)


def score_series(df: pd.DataFrame, params: DetectorParams | None = None) -> pd.DataFrame:
    """Add per-bar detector columns: logit, delta, z, volume_ratio, vol_conf, persistence, score."""
    p = params or DetectorParams()
    out = df.copy()
    out["logit"] = transform(out["price"], p)
    # Work on a short rolling median of log-odds so a single stray print can't
    # serve as either end of a "move".
    out["level"] = out["logit"].rolling(p.smooth, min_periods=1).median()
    out["delta"] = out["level"] - out["level"].shift(p.window)

    # Big? Compare this window's change with past window changes. Lag by `window`
    # so the baseline never overlaps the move being scored.
    out["z"] = robust_z(out["delta"], p.baseline, p.min_scale, lag=p.window)

    # Real money? Window volume vs. its usual level (median of past windows). The floor on "usual"
    # is a dollar amount converted to shares at the current price, so 500 contracts at 1c ($5) earn
    # nothing while the ratio itself stays in shares and is not distorted by the price move.
    if "volume" in out and out["volume"].notna().any():
        vol_win = out["volume"].fillna(0).rolling(p.window).sum()
        usual = vol_win.shift(p.window).rolling(p.baseline, min_periods=p.baseline // 2).median()
        out["volume_ratio"] = vol_win / usual.clip(lower=floor_shares(out["price"], p.min_notional, p.space))
        # No excess volume (ratio <= 1) earns nothing; vol_min_ratio or more earns full credit.
        out["vol_conf"] = ((out["volume_ratio"] - 1) / (p.vol_min_ratio - 1)).clip(0, 1)
    else:
        # No volume at all (old cache rows, hand-built frames): treat it as neutral so the
        # detector still runs.
        out["volume_ratio"] = np.nan
        out["vol_conf"] = 1.0

    # Sticks? Share of the move still in place `hold` bars later.
    start_level = out["level"].shift(p.window)
    later_move = out["level"].shift(-p.hold) - start_level
    with np.errstate(divide="ignore", invalid="ignore"):
        out["persistence"] = (later_move / out["delta"]).clip(0, 1).fillna(0)

    out["score"] = out["z"].abs() * out["vol_conf"] * out["persistence"]
    # Tick noise? One cent at 1% is ~0.7 logit, so a contract pinned in a tail would fire on every
    # tick. A move is scored only if at least one end sits inside [tail, 1-tail].
    if p.space == "logit":
        tail_now = in_tail(out["price"], p.tail)
        out.loc[tail_now & in_tail(out["price"].shift(p.window), p.tail), "score"] = 0.0
    return out


def floor_shares(price: pd.Series, dollars: float, space: str = "logit") -> pd.Series:
    """How many shares `dollars` at risk buys at this price.

    For a contract the smaller side (min(p, 1-p)) puts up the stake; for a stock the stake is the price.
    """
    if space == "log":
        return dollars / price
    return dollars / np.minimum(price, 1 - price)


def in_tail(price: pd.Series, tail: float) -> pd.Series:
    return (price < tail) | (price > 1 - tail)


def _unit(p: DetectorParams) -> str:
    return "usd" if p.space == "log" else "prob"


def _onset(level: pd.Series, first_hot: int, p: DetectorParams) -> int:
    """Index of the last bar before the move began.

    The move lies inside the window that first cleared the threshold, `[first_hot - window, first_hot]`.
    The peak bar can sit a whole window later while the score stays flat, so `peak - window` would put
    the start *after* the real onset; `first_hot - window` puts it a window too early. Inside that
    window the onset is the bar before the price had covered `onset_frac` of the window's change.
    """
    w0 = max(0, first_hot - p.window)
    seg = level.iloc[w0 : first_hot + 1].to_numpy()
    total = seg[-1] - seg[0]
    if total == 0 or np.isnan(total):
        return w0
    moved = np.flatnonzero((seg - seg[0]) / total >= p.onset_frac)
    return w0 + max(0, int(moved[0]) - 1) if moved.size else w0


def detect_jumps(df: pd.DataFrame, market_id: str = "", params: DetectorParams | None = None) -> list[Spike]:
    p = params or DetectorParams()
    s = score_series(df, p)
    hot = s["score"] >= p.score_threshold
    spikes: list[Spike] = []
    idx = s.index
    i, n = 0, len(s)
    while i < n:
        if not hot.iloc[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and hot.iloc[j + 1]:
            j += 1
        k = int(s["score"].iloc[i : j + 1].values.argmax()) + i  # peak bar of this run
        start_i = _onset(s["logit"], i, p)
        spikes.append(
            Spike(
                market_id=market_id,
                kind="jump",
                start=idx[start_i],
                peak=idx[k],
                confirmed_at=idx[min(n - 1, k + p.hold)],
                p_before=float(s["price"].iloc[start_i]),
                p_after=float(s["price"].iloc[k]),
                z=float(s["z"].iloc[k]),
                volume_ratio=float(s["volume_ratio"].iloc[k]),
                persistence=float(s["persistence"].iloc[k]),
                score=float(s["score"].iloc[k]),
                unit=_unit(p),
            )
        )
        i = j + 1 + p.cooldown
    return spikes


def detect_drifts(df: pd.DataFrame, market_id: str = "", params: DetectorParams | None = None) -> list[Spike]:
    """Two-sided CUSUM on standardized log-odds changes over `drift_bar`-minute bars.

    Catches grinds with no single big jump. The reference mean is zero rather than
    a rolling median: a fair prediction-market price should have no drift, and a
    rolling median would quietly absorb the very trend we want to catch.

    Real money? A grind trades at a normal pace rather than in a burst, so unlike a jump
    it needs only its usual volume (floored at `min_notional` dollars at risk per `window`) for full credit.
    The alarm fires when CUSUM level × that credit reaches `cusum_h`, so a grind on an
    empty book never fires and a thinly traded one fires later.
    """
    p = params or DetectorParams()
    bars = df["price"].resample(f"{p.drift_bar}min").last().dropna()
    lg = transform(bars, p)
    d = lg.diff()
    hist = d.shift(1)
    scale = (MAD_TO_SIGMA * _rolling_mad(hist, p.drift_baseline)).clip(lower=p.min_scale)
    x = (d / scale).where(scale.notna() & hist.rolling(p.drift_baseline, min_periods=16).count().ge(16), 0)
    # Same tail rule as the jumps: a change between two tail prices is a tick, not a step.
    if p.space == "logit":
        x = x.where(~(in_tail(bars, p.tail) & in_tail(bars.shift(1), p.tail)), 0)
    x = x.fillna(0).values

    has_volume = "volume" in df and df["volume"].notna().any()
    if has_volume:
        vol = df["volume"].fillna(0).resample(f"{p.drift_bar}min").sum().reindex(bars.index, fill_value=0)
        floor = floor_shares(bars, p.min_notional * p.drift_bar / p.window, p.space)
        usual = vol.shift(1).rolling(p.drift_baseline, min_periods=16).median().clip(lower=floor).fillna(floor)
        vol, usual = vol.values, usual.values

    spikes: list[Spike] = []
    pos = neg = 0.0
    pos_start = neg_start = 0
    idx = bars.index
    for t in range(len(x)):
        if pos == 0:
            pos_start = t
        if neg == 0:
            neg_start = t
        pos = max(0.0, pos + x[t] - p.cusum_k)
        neg = max(0.0, neg - x[t] - p.cusum_k)
        level = max(pos, neg)
        s0 = pos_start if pos >= neg else neg_start
        if has_volume:
            volume_ratio = vol[s0 : t + 1].sum() / usual[s0 : t + 1].sum()
            vol_conf = min(1.0, volume_ratio)
        else:
            volume_ratio, vol_conf = float("nan"), 1.0
        if level * vol_conf >= p.cusum_h:
            # Bar s0 is the first *change* in the run, so the move starts at the close before it.
            b0 = max(0, s0 - 1)
            spikes.append(
                Spike(
                    market_id=market_id,
                    kind="drift",
                    start=idx[b0],
                    peak=idx[t],
                    confirmed_at=idx[t],
                    p_before=float(bars.iloc[b0]),
                    p_after=float(bars.iloc[t]),
                    z=float(level),
                    volume_ratio=float(volume_ratio),
                    persistence=1.0,
                    score=float(level * vol_conf),
                    unit=_unit(p),
                )
            )
            pos = neg = 0.0
    return spikes


def detect(df: pd.DataFrame, market_id: str = "", params: DetectorParams | None = None) -> list[Spike]:
    """Run both detectors. Drift alerts that overlap a jump alert are dropped (the jump explains them)."""
    p = params or DetectorParams()
    jumps = detect_jumps(df, market_id, p)
    drifts = [
        d for d in detect_drifts(df, market_id, p)
        if not any(j.start <= d.peak <= j.confirmed_at + pd.Timedelta(minutes=p.cooldown) for j in jumps)
    ]
    return sorted(jumps + drifts, key=lambda s: s.peak)
