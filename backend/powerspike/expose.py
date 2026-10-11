"""Expose: who is hit by this event? Sector/credit ETFs from config/exposure.yaml plus
companies whose filings name the risk, via SEC EDGAR full-text search.

EDGAR FTS (free; SEC requires a User-Agent with a name and email, set SEC_USER_AGENT in .env).
Docs-verified, not live-tested:
  https://efts.sec.gov/LATEST/search-index?q=<query>&forms=10-K&dateRange=custom&startdt=YYYY-MM-DD&enddt=YYYY-MM-DD
  returns Elasticsearch-style {"hits": {"hits": [{"_source": {"display_names", "ciks", "form", "file_date"}}]}}

TODO: pre-compute and cache the company list per event type before demo day.
TODO: rank companies by how often / how prominently they mention the risk, or
      intersect with an issuer list (e.g. HYG holdings) to keep it credit-relevant.
"""

from __future__ import annotations

from collections import Counter

import httpx

from .config import SEC_USER_AGENT, exposure, market

EDGAR_FTS = "https://efts.sec.gov/LATEST/search-index"


def etfs(event_type: str) -> list[str]:
    return exposure().get(event_type, {}).get("etfs", [])


def all_etfs() -> list[str]:
    """Every ETF named in exposure.yaml, deduplicated, in config order (what pull_data.py fetches)."""
    return list(dict.fromkeys(t for cfg in exposure().values() for t in cfg.get("etfs", [])))


def note(event_type: str) -> str:
    return exposure().get(event_type, {}).get("direction_note", "")


def expected(event_type: str) -> dict[str, int]:
    """Sign of each ETF's move when the event's odds rise; a ticker not listed has no view (0)."""
    return {k: int(v) for k, v in exposure().get(event_type, {}).get("expected", {}).items()}


def event_sign(market_id: str) -> int:
    """+1 if the market's YES price rises with the event's odds, -1 if it falls; +1 for unknown markets."""
    try:
        return int(market(market_id).get("event_sign", 1))
    except KeyError:
        return 1


def edgar_companies(event_type: str, start: str = "2024-01-01", end: str = "2026-12-31", limit: int = 10) -> list[dict]:
    """Companies whose recent filings match the event's EDGAR query, most matches first."""
    cfg = exposure().get(event_type)
    if not cfg or not SEC_USER_AGENT:
        return []
    params = {"q": cfg["edgar_query"], "forms": ",".join(cfg.get("forms", ["10-K"])),
              "dateRange": "custom", "startdt": start, "enddt": end}
    try:
        r = httpx.get(EDGAR_FTS, params=params, headers={"User-Agent": SEC_USER_AGENT}, timeout=30)
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
    except (httpx.HTTPError, ValueError):
        return []
    counts: Counter[str] = Counter()
    for h in hits:
        for name in h.get("_source", {}).get("display_names", []):
            counts[name] += 1
    return [{"company": n, "filings": c} for n, c in counts.most_common(limit)]


def exposed(event_type: str) -> dict:
    return {"label": exposure().get(event_type, {}).get("label", event_type),
            "etfs": etfs(event_type), "expected": expected(event_type),
            "companies": edgar_companies(event_type), "note": note(event_type)}
