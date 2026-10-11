"""Synthetic market series for tests and for the dashboard before real data is cached.

The demo series contains, on purpose:
  * a real jump (big, high volume, holds)  -> should fire a "jump" alert
  * a fat-finger print (big, low volume, reverts at once) -> should NOT fire
  * a slow grind with no single big bar     -> should fire a "drift" alert
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _to_price(lg: np.ndarray) -> np.ndarray:
    p = 1 / (1 + np.exp(-lg))
    return np.round(p, 3)  # venues quote to ~0.1¢-1¢


def quiet_market(n: int = 1440, p0: float = 0.4, noise: float = 0.01, seed: int = 0,
                 start: str = "2025-12-10 12:00", freq: str = "1min") -> pd.DataFrame:
    """A random walk in log-odds with Poisson-ish volume. No events."""
    rng = np.random.default_rng(seed)
    lg = np.log(p0 / (1 - p0)) + np.cumsum(rng.normal(0, noise, n))
    vol = rng.gamma(shape=2.0, scale=50.0, size=n)
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"price": _to_price(lg), "volume": vol}, index=idx)


def add_jump(df: pd.DataFrame, at: int, size: float, over: int = 10, vol_mult: float = 6.0) -> pd.DataFrame:
    """Shift log-odds by `size` spread over `over` bars starting at bar `at`, and keep it there."""
    out = df.copy()
    lg = np.log(out["price"] / (1 - out["price"])).to_numpy(copy=True)
    ramp = np.clip((np.arange(len(lg)) - at) / over, 0, 1) * size
    out["price"] = _to_price(lg + ramp)
    out.iloc[at : at + over + 5, out.columns.get_loc("volume")] *= vol_mult
    return out


def add_fat_finger(df: pd.DataFrame, at: int, size: float, length: int = 2) -> pd.DataFrame:
    """A brief print far from the market that reverts immediately, on normal volume."""
    out = df.copy()
    lg = np.log(out["price"] / (1 - out["price"])).to_numpy(copy=True)
    lg[at : at + length] += size
    out["price"] = _to_price(lg)
    return out


def add_drift(df: pd.DataFrame, at: int, size: float, over: int = 240) -> pd.DataFrame:
    """A slow steady grind of `size` log-odds over `over` bars, normal volume."""
    out = df.copy()
    lg = np.log(out["price"] / (1 - out["price"])).to_numpy(copy=True)
    ramp = np.clip((np.arange(len(lg)) - at) / over, 0, 1) * size
    out["price"] = _to_price(lg + ramp)
    return out


def demo_market(seed: int = 7) -> pd.DataFrame:
    """One trading day with a fat-finger, a real jump and a slow drift.

    Starts at 03:00 UTC so the jump (score peak 14:54 UTC = 09:54 EST) lands
    inside the NYSE session and the synthetic ETFs in `demo_equity` can react to it; the drift
    still fires after the close and shows the after-hours path.
    """
    df = quiet_market(n=1440, p0=0.38, noise=0.006, seed=seed, start="2025-12-10 03:00")
    df = add_fat_finger(df, at=420, size=1.2)
    df = add_jump(df, at=700, size=0.95, over=12)   # ~38% -> ~61%
    df = add_drift(df, at=1000, size=-0.8, over=300)
    return df


# ---- synthetic ETFs for the lead/lag demo ---------------------------------------------

def equity_sessions(days: list[str], p0: float = 100.0, noise: float = 0.0003, seed: int = 0) -> pd.DataFrame:
    """A log-price random walk over the given NYSE sessions (09:30-16:00 ET), 1-minute bars, session-only."""
    rng = np.random.default_rng(seed)
    idx = pd.DatetimeIndex([])
    for d in days:
        idx = idx.append(pd.date_range(f"{d} 09:30", f"{d} 15:59", freq="1min", tz="America/New_York"))
    idx = idx.tz_convert("UTC")
    lg = np.log(p0) + np.cumsum(rng.normal(0, noise, len(idx)))
    vol = rng.gamma(shape=2.0, scale=5000.0, size=len(idx))
    return pd.DataFrame({"price": np.round(np.exp(lg), 2), "volume": vol}, index=idx)


def add_equity_move(df: pd.DataFrame, at: pd.Timestamp, size: float, over: int = 3) -> pd.DataFrame:
    """Shift log price by `size` over `over` bars starting at the first bar >= `at`, and keep it."""
    out = df.copy()
    pos = int(out.index.searchsorted(pd.Timestamp(at)))
    lg = np.log(out["price"].to_numpy(copy=True))
    ramp = np.clip((np.arange(len(lg)) - pos) / over, 0, 1) * size
    out["price"] = np.round(np.exp(lg + ramp), 2)
    return out


# Minutes after the demo jump *onset* (bar 700 = 14:40 UTC) at which each fed_rates ETF starts moving,
# and by how much (log return). XLF stays flat on purpose.
DEMO_REACTIONS = {"TLT": (8, 0.010), "IEF": (10, 0.004), "HYG": (15, 0.006), "LQD": (20, 0.007),
                  "KRE": (6, -0.012), "XLF": (None, 0.0)}


def demo_equity(ticker: str, seed: int = 11) -> pd.DataFrame:
    """A synthetic ETF for the `synthetic-demo` market: two sessions, reacting after the 14:40 UTC jump onset."""
    df = equity_sessions(["2025-12-09", "2025-12-10"], seed=seed + sum(map(ord, ticker.upper())))
    lag, size = DEMO_REACTIONS.get(ticker.upper(), (None, 0.0))
    if lag is None:
        return df
    return add_equity_move(df, at=pd.Timestamp("2025-12-10 14:40", tz="UTC") + pd.Timedelta(minutes=lag), size=size)
