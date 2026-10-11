"""Replay clock for the demo: walk a cached day forward bar by bar and surface alerts
only once they could have been known live (Spike.confirmed_at <= now).

The detector and explainer run for real on the historical data; only the clock is replayed.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pandas as pd

from .detect import DetectorParams, Spike, detect


@dataclass
class Frame:
    now: pd.Timestamp
    visible: pd.DataFrame       # data up to `now`
    alerts: list[Spike]         # alerts confirmed by `now`


def replay(df: pd.DataFrame, market_id: str, step: int = 5, params: DetectorParams | None = None) -> Iterator[Frame]:
    """Yield frames every `step` bars.

    Spikes are computed once over the full series and then revealed by
    confirmed_at. That is equivalent to running live because every detector
    input before confirmed_at is already in the past at that time.
    TODO: add a check that re-runs detect() on truncated data and asserts the same alerts.
    """
    spikes = detect(df, market_id, params)
    for i in range(step, len(df) + 1, step):
        now = df.index[i - 1]
        yield Frame(now=now, visible=df.iloc[:i], alerts=[s for s in spikes if s.confirmed_at <= now])
