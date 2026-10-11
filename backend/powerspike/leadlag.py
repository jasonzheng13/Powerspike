"""Lead/lag: did the prediction market move before or after the ETFs it should hit?

For one `Spike` and one ETF bar frame (see ingest/equities.py, session bars only):

  1. The ETF's intraday move is its `window`-bar log return *within the session* (the first
     `window` bars of a session have none), scored with the detector's own robust z
     (`detect.robust_z`), so "unusual" means unusual for that ETF. The opening bar instead carries
     the overnight *gap* (log open minus the previous close), scored against the intraday scale
     stretched to one session's worth of diffusion (`× sqrt(390 / window)`, a heuristic) with its
     own lower threshold `gap_sig_z`, because there is only one gap per session to be wrong about.
  2. Timing is onset against onset, in **trading minutes**: the ETF reacted at the first bar in the
     search window whose |z| clears the threshold, and `lag_min` is that bar's position minus the
     position of the market's `start` (the start of the market move), positive when the ETF moved
     after the market. The search window is `lookaround` session bars either side of that origin,
     so a spike shortly before the close carries the rest of its hour into the next morning
     (`spans_close`). A spike outside the session (prediction markets trade 24/7) is measured from
     the next open and flagged `after_hours`; its first candidate is the opening gap at lag 0.
  3. `z` and `ret` are the biggest move inside the first reacting run of bars, so the sign that
     feeds `consistent` belongs to the move that set the timing.
  4. `consistent` compares that sign with the config prior
     (market direction x markets.yaml event_sign x exposure.yaml expected[etf]).
  5. `status` separates "reacted", "quiet" (enough bars, nothing unusual) and "no_data" (fewer than
     `min_bars` scored bars after the origin: nothing cached, a holiday, or the cache ends early).
  6. The search looks `lookaround` bars past the origin, so each result carries its own
     `confirmed_at`: the timestamp of the last bar searched (next morning if the window spans the
     close), never earlier than the spike's own `confirmed_at`.

Per spike (`lead_lag`): a verdict needs at least `min_reactions` reacting ETFs (one quiet window in
five or six still clears 3.5 by chance about a third of the time); an after-hours spike that got a
reaction is "market led (overnight)", since the market moved while the stocks were closed; otherwise
the median lag over reacting ETFs gives "market led" / "market lagged" / "concurrent" (within
±`concurrent_min`). `summary()` aggregates the buckets for the proof slide.

Known limits: one opening gap answers every overnight spike that preceded it, so two markets moving
in the same night share a reaction; the gap scale is a heuristic, not a fitted model.

Cross-correlation over lags was rejected for now: the market series is bursty and forward-filled,
so 1-minute differences are mostly zero and a correlation would hinge on a handful of bars.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .detect import Spike, robust_scale, robust_z
from .ingest.equities import NY, in_session, next_open

SESSION_BARS = 390  # 09:30-16:00 at one bar a minute


@dataclass
class LeadLagParams:
    lookaround: int = 60        # session bars either side of the market onset to look for the ETF's reaction
    window: int = 15            # bars over which the ETF's intraday log return is measured
    baseline: int = 240         # bars of history for the ETF's rolling median / MAD
    min_scale: float = 0.001    # floor on the robust scale, in log-return units (0.1%)
    sig_z: float = 3.5          # |z| at or above this is an intraday reaction (on the cached ETFs a quiet
                                # 2 h window clears 2.5 about 24% of the time, 3.5 about 7%)
    gap_sig_z: float = 2.0      # |z| for the opening gap, scaled to one session of diffusion
    min_bars: int = 30          # scored bars needed after the origin before "quiet" means anything
    min_reactions: int = 2      # reacting ETFs needed for a verdict (or all of them when fewer are configured)
    concurrent_min: float = 2   # |median lag| within this many trading minutes is "concurrent"


@dataclass
class EtfReaction:
    ticker: str
    status: str                     # "reacted" | "quiet" | "no_data"
    etf_peak: pd.Timestamp | None   # first bar where the ETF's move became detectable
    lag_min: float | None           # trading minutes after the market onset (or the next open); negative = ETF first
    z: float | None                 # largest |robust z| in the first reacting run
    ret: float | None               # the ETF's log return at that bar (intraday window, or the opening gap)
    expected_move: int              # +1 / -1 / 0 from config, for this spike's direction
    consistent: bool | None
    after_hours: bool               # market onset outside the session; measured from the next open
    spans_close: bool               # the search window continued into the next session
    confirmed_at: pd.Timestamp


@dataclass
class LeadLag:
    market_id: str
    kind: str
    peak: pd.Timestamp
    reactions: list[EtfReaction]
    verdict: str                    # "market led" | "market lagged" | "concurrent" | "market led (overnight)"
                                    # | "no equity move" | "no data"
    median_lag: float | None
    confirmed_at: pd.Timestamp


def equity_z(df: pd.DataFrame, params: LeadLagParams | None = None) -> pd.DataFrame:
    """Per bar: `move` (intraday window return, or the opening gap on a session's first bar) and its `z`.

    Columns: level, delta (NaN in a session's first `window` bars), z, gap (first bar only), gap_z,
    move, mz (the z that applies to `move`), hot (|mz| clears its threshold).
    """
    p = params or LeadLagParams()
    out = df.copy()
    out["level"] = np.log(out["price"].astype(float))
    session = pd.Series(out.index.tz_convert(NY).date, index=out.index)
    out["delta"] = out["level"] - out.groupby(session)["level"].shift(p.window)
    out["z"] = robust_z(out["delta"], p.baseline, p.min_scale, lag=p.window)
    first = session.ne(session.shift(1)) & session.shift(1).notna()
    out["gap"] = (out["level"] - out["level"].shift(1)).where(first)
    gap_scale = robust_scale(out["delta"], p.baseline, p.min_scale, lag=p.window) * math.sqrt(SESSION_BARS / p.window)
    out["gap_z"] = out["gap"] / gap_scale
    out["move"] = out["gap"].where(first, out["delta"])
    out["mz"] = out["gap_z"].where(first, out["z"])
    out["hot"] = (out["mz"].abs() >= np.where(first, p.gap_sig_z, p.sig_z)) & out["mz"].notna()
    return out


def react(spike: Spike, etf: pd.DataFrame, ticker: str, expected_move: int,
          params: LeadLagParams | None = None) -> EtfReaction:
    p = params or LeadLagParams()
    onset = pd.Timestamp(spike.start)
    after_hours = not in_session(onset)
    origin = next_open(onset)
    fallback_confirm = max(pd.Timestamp(spike.confirmed_at), origin + pd.Timedelta(minutes=p.lookaround))

    def none(status: str, confirmed_at: pd.Timestamp, spans_close: bool = False) -> EtfReaction:
        return EtfReaction(ticker, status, None, None, None, None, expected_move, None, after_hours, spans_close, confirmed_at)

    if etf.empty:
        return none("no_data", fallback_confirm)
    s = equity_z(etf, p)
    idx = s.index
    pos = int(idx.searchsorted(origin))
    if pos >= len(idx):
        return none("no_data", fallback_confirm)
    lo, hi = (pos if after_hours else max(0, pos - p.lookaround)), pos + p.lookaround
    complete = hi < len(idx)
    confirmed_at = max(pd.Timestamp(spike.confirmed_at), idx[hi]) if complete else fallback_confirm
    spans_close = complete and idx[hi].tz_convert(NY).date() != idx[pos].tz_convert(NY).date()
    win = s.iloc[lo : hi + 1]
    forward = win.iloc[pos - lo :]
    if forward["mz"].notna().sum() < min(p.min_bars, p.lookaround + 1) and not forward["hot"].any():
        return none("no_data", confirmed_at, spans_close)
    hot = win.index[win["hot"].to_numpy()]
    if len(hot) == 0:
        return none("quiet", confirmed_at, spans_close)
    first = hot[0]
    first_i = int(idx.get_loc(first))
    run_end = first_i
    while run_end + 1 <= hi and run_end + 1 < len(idx) and bool(s["hot"].iloc[run_end + 1]):
        run_end += 1
    run = s.iloc[first_i : run_end + 1]
    big = run["mz"].abs().idxmax()
    ret, z = float(run.loc[big, "move"]), float(run.loc[big, "mz"])
    consistent = None if expected_move == 0 else bool(np.sign(ret) == expected_move)
    return EtfReaction(ticker, "reacted", first, float(first_i - pos), z, ret, expected_move,
                       consistent, after_hours, spans_close, confirmed_at)


def lead_lag(spike: Spike, equities: dict[str, pd.DataFrame], expected: dict[str, int],
             event_sign: int = 1, params: LeadLagParams | None = None) -> LeadLag:
    p = params or LeadLagParams()
    market_sign = 1 if spike.direction == "up" else -1
    reactions = [react(spike, df, t, market_sign * event_sign * int(expected.get(t, 0)), p) for t, df in equities.items()]
    reacting = [r for r in reactions if r.status == "reacted"]
    lags = [r.lag_min for r in reacting if r.lag_min is not None]
    median = float(np.median(lags)) if lags else None
    needed = min(p.min_reactions, len(reactions))
    if reactions and all(r.status == "no_data" for r in reactions):
        verdict = "no data"
    elif len(reacting) < max(1, needed):
        verdict = "no equity move"
    elif reacting[0].after_hours:
        verdict = "market led (overnight)"
    elif median is not None and median > p.concurrent_min:
        verdict = "market led"
    elif median is not None and median < -p.concurrent_min:
        verdict = "market lagged"
    else:
        verdict = "concurrent"
    confirmed_at = max([r.confirmed_at for r in reactions], default=pd.Timestamp(spike.confirmed_at))
    return LeadLag(spike.market_id, spike.kind, pd.Timestamp(spike.peak), reactions, verdict, median, confirmed_at)


def summary(results: list[LeadLag]) -> dict:
    """Proof-slide numbers across spikes: how often the market led, and by how much (in-session only)."""
    verdicts = [r.verdict for r in results]
    lags = [r.median_lag for r in results if r.median_lag is not None and r.verdict in ("market led", "market lagged", "concurrent")]
    checks = [x.consistent for r in results for x in r.reactions if x.consistent is not None]
    return {
        "spikes": len(results),
        "led": verdicts.count("market led"),
        "led_overnight": verdicts.count("market led (overnight)"),
        "lagged": verdicts.count("market lagged"),
        "concurrent": verdicts.count("concurrent"),
        "no_move": verdicts.count("no equity move"),
        "no_data": verdicts.count("no data"),
        "median_lag_min": float(np.median(lags)) if lags else None,
        "consistent_share": (sum(checks) / len(checks)) if checks else None,
    }


def run_cached(con, markets: list[dict], params: LeadLagParams | None = None,
               detector_params=None) -> list[LeadLag]:
    """Detect on every cached market and score each spike against the cached ETFs of its event type.

    `markets` are entries from config/markets.yaml (`id`, `event_type`). Markets with nothing
    cached are skipped; ETFs with nothing cached still appear in each result as `no_data`.
    """
    from . import expose
    from .detect import detect
    from .ingest import cache
    from .ingest.equities import equity_id

    out: list[LeadLag] = []
    for m in markets:
        df = cache.load(con, m["id"])
        if df.empty:
            continue
        equities = {t: cache.load(con, equity_id(t)) for t in expose.etfs(m["event_type"])}
        for s in detect(df, m["id"], detector_params):
            out.append(lead_lag(s, equities, expose.expected(m["event_type"]), expose.event_sign(m["id"]), params))
    return out


# ---- JSON ---------------------------------------------------------------------------------

def _iso(ts) -> str | None:
    return None if ts is None else pd.Timestamp(ts).tz_convert("UTC").isoformat()


def _num(x) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def to_json(ll: LeadLag) -> dict:
    d = asdict(ll)
    d["peak"], d["confirmed_at"] = _iso(ll.peak), _iso(ll.confirmed_at)
    d["median_lag"] = _num(ll.median_lag)
    d["reactions"] = [
        {**asdict(r), "etf_peak": _iso(r.etf_peak), "confirmed_at": _iso(r.confirmed_at),
         "lag_min": _num(r.lag_min), "z": _num(r.z), "ret": _num(r.ret)}
        for r in ll.reactions
    ]
    return d
