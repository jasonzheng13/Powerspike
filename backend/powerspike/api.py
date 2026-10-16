"""HTTP API for the TypeScript frontend.   cd backend && uvicorn powerspike.api:app --reload

The detector, explainer and exposure lookup run here; the browser only owns the
replay clock. Every alert carries `confirmed_at`, and the frontend filters on
`confirmed_at <= now` (lib/replay.ts), so the demo stays free of look-ahead.

If `frontend/dist` exists (``npm run build``) it is served at `/`, so one process
hosts the whole demo.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, expose, leadlag
from .detect import DetectorParams, Spike, detect
from .explain import explain, headlines, news_window
from .ingest import cache
from .ingest.equities import equity_id
from .synthetic import demo_equity, demo_market

SYNTHETIC = {"id": "synthetic-demo", "name": "Fed cuts in December (synthetic demo)",
             "venue": "synthetic", "event_type": "fed_rates"}
DEFAULT_NEWS_QUERY = '"Federal Reserve" OR Powell'

app = FastAPI(title="Powerspike API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- helpers ---------------------------------------------------------------------------

def _num(x: float) -> float | None:
    """JSON has no NaN; send null instead."""
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def _iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").isoformat()


def spike_to_json(s: Spike) -> dict[str, Any]:
    d = s.to_dict()
    for k in ("start", "peak", "confirmed_at"):
        d[k] = _iso(d[k])
    for k in ("p_before", "p_after", "z", "volume_ratio", "persistence", "score"):
        d[k] = _num(d[k])
    d["headline"] = s.headline()
    return d


def spike_from_json(d: dict[str, Any]) -> Spike:
    fields = {k: d[k] for k in ("market_id", "kind", "p_before", "p_after", "z", "persistence", "score")}
    fields.update({k: pd.Timestamp(d[k]) for k in ("start", "peak", "confirmed_at")})
    vr = d.get("volume_ratio")
    fields["volume_ratio"] = float("nan") if vr is None else float(vr)
    return Spike(**fields)


def _markets() -> list[dict]:
    try:
        cached = set(cache.cached_markets(cache.connect()))
    except Exception:
        cached = set()
    real = [{k: m[k] for k in ("id", "name", "venue", "event_type")} for m in config.markets() if m["id"] in cached]
    return real + [SYNTHETIC]


def _market(market_id: str) -> dict:
    for m in _markets():
        if m["id"] == market_id:
            return m
    raise HTTPException(404, f"unknown or uncached market {market_id!r}")


@lru_cache(maxsize=32)
def _load(market_id: str) -> pd.DataFrame:
    if market_id == SYNTHETIC["id"]:
        return demo_market()
    return cache.load(cache.connect(), market_id)


@lru_cache(maxsize=64)
def _load_equity(market_id: str, ticker: str) -> pd.DataFrame:
    """ETF bars for a market's lead/lag: synthetic ETFs for the synthetic market, the cache otherwise."""
    if market_id == SYNTHETIC["id"]:
        return demo_equity(ticker) if ticker in expose.etfs(SYNTHETIC["event_type"]) else pd.DataFrame(columns=["price", "volume"])
    return cache.load(cache.connect(), equity_id(ticker))


@lru_cache(maxsize=256)
def _leadlag_cached(key: str) -> dict:
    body = json.loads(key)
    s = spike_from_json(body["spike"])
    m = _market(body["market_id"])
    equities = {t: _load_equity(m["id"], t) for t in expose.etfs(m["event_type"])}
    ll = leadlag.lead_lag(s, equities, expose.expected(m["event_type"]), expose.event_sign(m["id"]))
    return leadlag.to_json(ll)


@lru_cache(maxsize=256)
def _explain_cached(key: str) -> tuple[str, list[dict]]:
    body = json.loads(key)
    s = spike_from_json(body["spike"])
    start, end = news_window(s)
    heads = headlines(body["query"], start, end) if body["query"] else []
    why = explain(s, body["market_name"], heads, expose.note(body["event_type"]))
    return why, heads


@lru_cache(maxsize=32)
def _exposed_cached(event_type: str) -> dict:
    # One EDGAR call per event type per process, instead of one per card per tick.
    return expose.exposed(event_type)


# ---- routes ----------------------------------------------------------------------------

@app.get("/api/markets")
def list_markets() -> dict:
    return {"markets": _markets()}


@app.get("/api/store")
def store() -> dict:
    """Where the bars live and how fresh each market is. `backend` is 'tiger' (Tiger Cloud hypertable +
    bars_15min continuous aggregate) or 'duckdb' (local file); `markets` is empty when nothing is cached."""
    try:
        rows = cache.stats(cache.connect())
    except Exception:
        rows = []
    return {"backend": cache.backend(), "markets": rows}


@app.get("/api/markets/{market_id}/series")
def market_series(market_id: str) -> dict:
    _market(market_id)
    df = _load(market_id)
    if df.empty:
        raise HTTPException(404, f"no data cached for {market_id!r}")
    has_volume = "volume" in df and df["volume"].notna().any()
    return {
        "market_id": market_id,
        "ts": [_iso(t) for t in df.index],
        "price": [float(p) for p in df["price"]],
        "volume": [_num(v) for v in df["volume"]] if has_volume else None,
    }


@app.get("/api/markets/{market_id}/alerts")
def market_alerts(
    market_id: str,
    window: int = Query(15, ge=1, le=240),
    score_threshold: float = Query(4.0, ge=0),
    hold: int = Query(30, ge=1, le=1440),
    vol_min_ratio: float = Query(2.0, gt=1),
) -> dict:
    _market(market_id)
    df = _load(market_id)
    if df.empty:
        raise HTTPException(404, f"no data cached for {market_id!r}")
    p = DetectorParams(window=window, score_threshold=score_threshold, hold=hold, vol_min_ratio=vol_min_ratio)
    return {"market_id": market_id, "params": p.__dict__, "alerts": [spike_to_json(s) for s in detect(df, market_id, p)]}


@app.get("/api/markets/{market_id}/equities/{ticker}/series")
def equity_series(market_id: str, ticker: str) -> dict:
    _market(market_id)
    ticker = ticker.upper()
    df = _load_equity(market_id, ticker)
    if df.empty:
        raise HTTPException(404, f"no equity bars cached for {ticker!r}; run scripts/pull_data.py")
    return {
        "market_id": market_id,
        "ticker": ticker,
        "ts": [_iso(t) for t in df.index],
        "price": [float(p) for p in df["price"]],
        "volume": [_num(v) for v in df["volume"]],
    }


class LeadLagRequest(BaseModel):
    market_id: str
    spike: dict[str, Any]


@app.post("/api/leadlag")
def leadlag_for_spike(req: LeadLagRequest) -> dict:
    """Did the market lead or lag its exposed ETFs around this spike? Carries its own confirmed_at."""
    _market(req.market_id)
    return _leadlag_cached(json.dumps({"market_id": req.market_id, "spike": req.spike}, sort_keys=True, default=str))


class ExplainRequest(BaseModel):
    market_id: str
    spike: dict[str, Any]
    query: str = DEFAULT_NEWS_QUERY


@app.post("/api/explain")
def explain_spike(req: ExplainRequest) -> dict:
    m = _market(req.market_id)
    key = json.dumps({"spike": req.spike, "query": req.query, "market_name": m["name"],
                      "event_type": m["event_type"]}, sort_keys=True, default=str)
    why, heads = _explain_cached(key)
    return {"why": why, "headlines": heads}


@app.get("/api/exposure/{event_type}")
def exposure(event_type: str) -> dict:
    if event_type not in config.exposure():
        raise HTTPException(404, f"unknown event type {event_type!r}")
    return _exposed_cached(event_type)


# ---- built frontend (optional) ---------------------------------------------------------

_DIST = config.REPO_ROOT / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
