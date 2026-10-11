"""Validation harness: score the detector on known events (the "proof slide").

For each event in validation/known_events.yaml, load the cached market data around
the event time, run the detector, and check whether an alert fired within tolerance.
Reports hit rate on expected events and false-alarm rate on quiet controls.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

from .config import ROOT
from .detect import DetectorParams, detect
from .ingest import cache


def run(path: Path = ROOT / "validation" / "known_events.yaml", params: DetectorParams | None = None,
        context_hours: int = 12) -> pd.DataFrame:
    spec = yaml.safe_load(path.read_text())
    tol = pd.Timedelta(minutes=spec.get("tolerance_min", 60))
    con = cache.connect()
    rows = []
    for ev in spec["events"]:
        t = pd.Timestamp(ev["time"])
        df = cache.load(con, ev["market"], t - pd.Timedelta(hours=context_hours), t + pd.Timedelta(hours=context_hours))
        if df.empty:
            rows.append({**ev, "fired": None, "note": "no cached data, run scripts/pull_data.py"})
            continue
        spikes = [s for s in detect(df, ev["market"], params) if abs(s.peak - t) <= tol]
        rows.append({**ev, "fired": bool(spikes), "best": spikes[0].headline() if spikes else "", "note": ""})
    return pd.DataFrame(rows)


def summary(results: pd.DataFrame) -> dict:
    scored = results.dropna(subset=["fired"])
    pos = scored[scored["expect_alert"]]
    neg = scored[~scored["expect_alert"]]
    return {
        "events_scored": len(scored),
        "hit_rate": float(pos["fired"].mean()) if len(pos) else None,
        "false_alarm_rate": float(neg["fired"].mean()) if len(neg) else None,
    }
