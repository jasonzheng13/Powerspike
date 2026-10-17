"""HTTP API for the stock dashboard.   cd backend && uvicorn powerspike.api:app --reload

Search-driven: every stock route starts by bringing the ticker's bars up to date
(`stocks.refresh`) and uses the newest bar's timestamp as a *freshness token* in its cache keys,
so a re-pull shows up without a restart. The slow calls are the explanations (one GDELT request
per spike, spaced 5.5 s apart) and the outlook, which needs every spike's explanation; the frontend
asks for the cards first and the outlook last so the outlook hits the per-spike memo.

If `frontend/dist` exists (``npm run build``) it is served at `/`, so one process hosts the demo.
"""

from __future__ import annotations

import math
import time
from collections import OrderedDict
from dataclasses import asdict
from functools import lru_cache
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, leadlag, outlook, stocks
from .detect import EQUITY_PARAMS, Spike, detect_jumps
from .explain import AI_CLIMATE_QUERY, CLIMATE_HOURS, climate, explain_stock, headlines, news_window
from .ingest import cache
from .ingest.equities import equity_id

ALLOWED_DAYS = (7, 30)
CLIMATE_TTL_S = 1800
CONTRACT_WIRE_FREQ = "5min"   # the 1-minute contract grid is mostly forward-filled; the chart gets 5-minute points

app = FastAPI(title="Powerspike API", version="0.2.0")
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
    fields["unit"] = d.get("unit", "prob")
    return Spike(**fields)


def _con():
    return cache.connect()


def _now() -> pd.Timestamp:
    return stocks._now()


def _days(days: int) -> int:
    if days not in ALLOWED_DAYS:
        raise HTTPException(400, f"days must be one of {ALLOWED_DAYS}")
    return days


def _token(ticker: str, days: int) -> str:
    """Refresh the ticker's bars and return the freshness token (newest bar, ISO)."""
    try:
        return stocks.refresh(_con(), ticker, days, _now()).isoformat()
    except stocks.UnknownTicker:
        raise HTTPException(404, f"no price data for {ticker!r}")
    except Exception as e:  # yfinance transport errors: say so instead of a 500
        raise HTTPException(503, f"could not load {ticker!r}: {type(e).__name__}: {str(e)[:200]}")


@lru_cache(maxsize=64)
def _bars(ticker: str, days: int, token: str) -> pd.DataFrame:
    return cache.load(_con(), equity_id(ticker), start=pd.Timestamp(token) - pd.Timedelta(days=days))


@lru_cache(maxsize=64)
def _spikes(ticker: str, days: int, token: str) -> tuple[Spike, ...]:
    # Jumps only: drifts over session-only 15-minute bars alarm on routine overnight gaps.
    return tuple(detect_jumps(_bars(ticker, days, token), equity_id(ticker), EQUITY_PARAMS))


def _first_contract(ticker: str) -> dict | None:
    contracts = stocks.company(ticker)["contracts"]
    return contracts[0] if contracts else None


def _contract_token(contract: dict, days: int) -> str | None:
    """Refresh the contract's prices; None when the venue has nothing or the pull failed (never a 500)."""
    try:
        ts = stocks.ensure_contract(_con(), contract, days, _now())
    except Exception:
        return None
    return None if ts is None else ts.isoformat()


@lru_cache(maxsize=64)
def _contract_bars(contract_id: str, days: int, ctoken: str) -> pd.DataFrame:
    return cache.load(_con(), contract_id, start=pd.Timestamp(ctoken) - pd.Timedelta(days=days))


@lru_cache(maxsize=64)
def _reactions(ticker: str, days: int, token: str, ctoken: str | None) -> tuple[dict | None, ...]:
    """Per spike (aligned with `_spikes`): the contract's reaction as JSON, or None when no contract is configured."""
    spikes = _spikes(ticker, days, token)
    c = _first_contract(ticker)
    if c is None:
        return tuple(None for _ in spikes)
    cdf = _contract_bars(c["id"], days, ctoken) if ctoken else pd.DataFrame(columns=["price", "volume"])
    return tuple(leadlag.contract_to_json(leadlag.react_contract(s, cdf, c["id"], c["event_sign"])) for s in spikes)


EXPLAIN_MEMO_SIZE = 1024
_explain_memo: OrderedDict[str, dict] = OrderedDict()   # bounded LRU keyed on the spike's identity, server-detected spikes only


def _explanation(ticker: str, s: Spike) -> dict:
    """Headlines + theme-tagged explanation for a spike the server detected (never a client-supplied one)."""
    company = stocks.company(ticker)
    key = f"{ticker}|{s.kind}|{_iso(s.peak)}|{company['news_query']}"
    hit = _explain_memo.get(key)
    if hit is not None:
        _explain_memo.move_to_end(key)
        return hit
    start, end = news_window(s)
    heads = headlines(company["news_query"], start, end)
    out = {**explain_stock(s, company, heads), "headlines": heads}
    _explain_memo[key] = out
    while len(_explain_memo) > EXPLAIN_MEMO_SIZE:
        _explain_memo.popitem(last=False)
    return out


def _server_spike(ticker: str, days: int, token: str, ref: dict[str, Any]) -> Spike:
    """The server's own Spike matching a client's (kind, peak) reference, or 404."""
    try:
        kind, peak = str(ref["kind"]), pd.Timestamp(ref["peak"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "spike must carry 'kind' and an ISO 'peak'")
    for s in _spikes(ticker, days, token):
        if s.kind == kind and s.peak == peak:
            return s
    raise HTTPException(404, f"no {kind} spike at {ref.get('peak')} for {ticker!r}")


def _bucket() -> int:
    return int(time.time() // CLIMATE_TTL_S)


@lru_cache(maxsize=4)
def _climate(bucket: int) -> dict:
    now = _now()
    heads = headlines(AI_CLIMATE_QUERY, now - pd.Timedelta(hours=CLIMATE_HOURS), now, limit=40)
    return {**climate(heads), "headlines": heads, "as_of": _iso(now)}


@lru_cache(maxsize=64)
def _outlook(ticker: str, days: int, token: str, ctoken: str | None, bucket: int) -> dict:
    spikes = _spikes(ticker, days, token)
    reactions = _reactions(ticker, days, token, ctoken)
    company = stocks.company(ticker)
    pairs = [(s, _explanation(ticker, s)) for s in spikes]   # sequential: one GDELT slot each on a miss
    clim = _climate(bucket)
    contract_info = None
    trend = move_24h = led_share = None
    c = _first_contract(ticker)
    if c is not None and ctoken:
        sig = outlook.contract_signal(_contract_bars(c["id"], days, ctoken), c["event_sign"], pd.Timestamp(ctoken))
        reacted = [r for r in reactions if r and r["status"] == "reacted"]
        led_share = (sum(r["verdict"] == "contract led" for r in reacted) / len(reacted)) if reacted else None
        if sig:
            trend, move_24h = sig["trend_7d_pts"], sig["move_24h_pts"]
            contract_info = {**sig, "led_share": led_share, "id": c["id"], "name": c["name"]}
    last = pd.Timestamp(token)
    quiet = not any(s.peak >= last - pd.Timedelta(hours=24) for s in spikes)
    reacted_n = sum(1 for r in reactions if r and r["status"] == "reacted") if c is not None and ctoken else None
    o = outlook.score(outlook.sensitivity(pairs), clim["themes"], trend, led_share, contract_move_24h=move_24h,
                      stock_quiet_24h=quiet, n_spikes=len(spikes), reacted_n=reacted_n)
    return {
        "ticker": ticker, "name": company["name"], "label": o.label, "confidence": o.confidence, "score": o.score,
        "n_spikes": o.n_spikes, "drivers": [asdict(d) for d in o.drivers], "prose": outlook.prose(o, company["name"]),
        "climate_summary": clim["summary"], "contract": contract_info, "disclaimer": outlook.DISCLAIMER, "as_of": token,
    }


# ---- routes ----------------------------------------------------------------------------

@app.get("/api/companies")
def companies() -> dict:
    """Configured companies for search suggestions (token ids stay server-side)."""
    return {"companies": [
        {**{k: c[k] for k in ("ticker", "name", "sector", "news_query")},
         "contracts": [{k: x[k] for k in ("id", "name", "question", "event_sign")} for x in c["contracts"]]}
        for c in config.companies()
    ]}


@app.get("/api/stocks/{ticker}")
def stock_series(ticker: str, days: int = Query(30)) -> dict:
    ticker, days = ticker.upper(), _days(days)
    token = _token(ticker, days)
    df = _bars(ticker, days, token)
    company = stocks.company(ticker)
    contract = None
    c = _first_contract(ticker)
    if c is not None:
        ctoken = _contract_token(c, days)
        cdf = _contract_bars(c["id"], days, ctoken) if ctoken else pd.DataFrame(columns=["price", "volume"])
        if not cdf.empty:
            wire = cdf["price"].resample(CONTRACT_WIRE_FREQ).last().dropna()
            contract = {"id": c["id"], "name": c["name"], "question": c["question"], "event_sign": c["event_sign"],
                        "ts": [_iso(t) for t in wire.index], "price": [float(p) for p in wire]}
    return {
        "ticker": ticker, "name": company["name"], "configured": company["configured"], "sector": company["sector"],
        "as_of": token,
        "ts": [_iso(t) for t in df.index],
        "price": [float(p) for p in df["price"]],
        "volume": [_num(v) for v in df["volume"]],
        "contract": contract,
    }


@app.get("/api/stocks/{ticker}/spikes")
def stock_spikes(ticker: str, days: int = Query(30)) -> dict:
    ticker, days = ticker.upper(), _days(days)
    token = _token(ticker, days)
    c = _first_contract(ticker)
    ctoken = _contract_token(c, days) if c else None
    spikes = _spikes(ticker, days, token)
    reactions = _reactions(ticker, days, token, ctoken)
    return {"ticker": ticker, "as_of": token, "params": asdict(EQUITY_PARAMS),
            "spikes": [{**spike_to_json(s), "contract_leadlag": r} for s, r in zip(spikes, reactions)]}


class ExplainRequest(BaseModel):
    spike: dict[str, Any]


@app.post("/api/stocks/{ticker}/explain")
def stock_explain(ticker: str, req: ExplainRequest, days: int = Query(30)) -> dict:
    """Why this spike happened: headlines around it plus a theme-tagged explanation. Memoised per spike.

    The body's spike is only a reference (`kind`, `peak`); the server explains its own detected copy,
    so client fields never reach the prompt or the memo.
    """
    ticker, days = ticker.upper(), _days(days)
    token = _token(ticker, days)
    return _explanation(ticker, _server_spike(ticker, days, token, req.spike))


@app.get("/api/climate")
def ai_climate() -> dict:
    """The last 72 h of AI headlines summarised into themes; refreshed every 30 minutes."""
    return _climate(_bucket())


@app.get("/api/stocks/{ticker}/outlook")
def stock_outlook(ticker: str, days: int = Query(30)) -> dict:
    """Bullish / bearish / neutral for the stock now, from its spikes, the AI climate and its contract."""
    ticker, days = ticker.upper(), _days(days)
    token = _token(ticker, days)
    c = _first_contract(ticker)
    ctoken = _contract_token(c, days) if c else None
    return _outlook(ticker, days, token, ctoken, _bucket())


# ---- built frontend (optional) ---------------------------------------------------------

_DIST = config.REPO_ROOT / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
