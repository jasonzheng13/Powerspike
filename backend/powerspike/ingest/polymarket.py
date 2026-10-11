"""Polymarket client (no auth needed for reads).

Docs-verified, not yet live-tested:
  Gamma (market discovery): https://gamma-api.polymarket.com/markets
  CLOB price history:       https://clob.polymarket.com/prices-history
      params: market=<CLOB token id>, startTs, endTs (unix s), fidelity=<minutes>
      returns {"history": [{"t": <unix s>, "p": <price>}, ...]}  (no volume)

Live-tested 2026-10-09:
  Data API trades:          https://data-api.polymarket.com/trades
      params: market=<condition id>, start, end (unix s, inclusive), limit, offset
      returns newest first: [{"asset", "outcome", "side", "size", "price", "timestamp", "transactionHash", ...}]
      Covers both outcomes of the market. Default takerOnly=true lists each fill once.
      Offsets past ~10k return nothing, so trades_volume() pages backwards by `end` instead.

Gotcha: `market` is the CLOB token ID from the Gamma market's `clobTokenIds`
(a JSON-encoded list; index 0 is usually YES), not the market id or slug. The
trades endpoint wants the market's `conditionId` instead; condition_id() maps one to the other.
"""

from __future__ import annotations

import json
import time

import httpx
import pandas as pd

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA_API = "https://data-api.polymarket.com"


def search_markets(query: str = "", limit: int = 20, active: bool = True) -> list[dict]:
    """List markets by 24h volume; filter by a substring of the question."""
    params = {"limit": 200, "active": str(active).lower(), "closed": "false", "order": "volume24hr", "ascending": "false"}
    r = httpx.get(f"{GAMMA}/markets", params=params, timeout=30)
    r.raise_for_status()
    out = []
    for m in r.json():
        if query.lower() in m.get("question", "").lower():
            tokens = m.get("clobTokenIds")
            tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
            out.append({"question": m.get("question"), "slug": m.get("slug"), "token_ids": tokens, "volume24hr": m.get("volume24hr")})
    return out[:limit]


def price_history(token_id: str, start: pd.Timestamp, end: pd.Timestamp, fidelity: int = 1) -> pd.DataFrame:
    """Price history for one outcome token. Polymarket returns no volume here, so `volume` is NaN.

    TODO: the API may cap how long a window you can ask for at 1-minute fidelity;
    if so, page through [start, end] in chunks.
    """
    params = {"market": token_id, "startTs": int(start.timestamp()), "endTs": int(end.timestamp()), "fidelity": fidelity}
    r = httpx.get(f"{CLOB}/prices-history", params=params, timeout=30)
    r.raise_for_status()
    hist = r.json().get("history", [])
    df = pd.DataFrame(hist)
    if df.empty:
        return pd.DataFrame(columns=["price", "volume"])
    df.index = pd.to_datetime(df["t"], unit="s", utc=True)
    return pd.DataFrame({"price": df["p"].astype(float), "volume": float("nan")})


TRADES_PAGE = 1000
RETRIES = 5


def _get(url: str, params: dict) -> httpx.Response:
    """GET that waits out 429s (the Data API rate-limits bursts), honouring Retry-After when sent."""
    for attempt in range(RETRIES):
        r = httpx.get(url, params=params, timeout=30)
        if r.status_code != 429 or attempt == RETRIES - 1:
            break
        time.sleep(float(r.headers.get("Retry-After") or 2**attempt))
    r.raise_for_status()
    return r


def condition_id(token_id: str) -> str:
    """The market's condition id (what the trades endpoint keys on) for one of its CLOB tokens."""
    markets = _get(f"{GAMMA}/markets", {"clob_token_ids": token_id}).json()
    if not markets:
        raise ValueError(f"no Gamma market for token {token_id}")
    return markets[0]["conditionId"]


def trades_volume(condition_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    """Traded size (shares, both outcomes) per trade in [start, end], indexed by UTC time.

    A share pays $1, so this is the same unit as Kalshi's contract volume. Pages newest to
    oldest by moving `end` back to the oldest trade seen; trades sharing that second come
    back on the next page and are dropped as duplicates.
    """
    seen: set[tuple] = set()
    rows = []
    cursor = int(end.timestamp())
    while True:
        params = {"market": condition_id, "start": int(start.timestamp()), "end": cursor, "limit": TRADES_PAGE}
        page = _get(f"{DATA_API}/trades", params).json()
        for t in page:
            key = (t.get("transactionHash"), t.get("asset"), t.get("side"), t.get("size"), t.get("timestamp"), t.get("proxyWallet"))
            if key not in seen:
                seen.add(key)
                rows.append((t["timestamp"], float(t["size"])))
        if len(page) < TRADES_PAGE:
            break
        oldest = min(t["timestamp"] for t in page)
        # A full page inside one second would loop forever; step past it (may drop a few trades).
        cursor = oldest if oldest < cursor else cursor - 1
    if not rows:
        return pd.Series(dtype=float, name="volume")
    ts, size = zip(*rows)
    return pd.Series(size, index=pd.to_datetime(ts, unit="s", utc=True), name="volume").sort_index()


def history(token_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Price history plus traded volume, in the shape `ingest.to_grid` expects.

    Price rows carry volume 0 and trade rows carry NaN price, so on the grid price is
    last-in-bucket (NaN skipped) and volume is the sum of trades in the bucket.
    """
    prices = price_history(token_id, start, end)
    if prices.empty:
        return prices
    trades = trades_volume(condition_id(token_id), start, end)
    prices = prices.assign(volume=0.0)
    if trades.empty:
        return prices
    return pd.concat([prices, pd.DataFrame({"price": float("nan"), "volume": trades})]).sort_index(kind="stable")
