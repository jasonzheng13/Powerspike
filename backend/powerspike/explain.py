"""Explain: why did it move? Headlines from the spike window plus a Gemini summary.

Two callers: the macro alert card (`explain`, prose) and the stock dashboard (`explain_stock`,
structured JSON: why, theme, ai_related, stock_sentiment) plus the AI-news `climate` summary over
the last `CLIMATE_HOURS`. Every Gemini call goes through `_generate`, the one seam tests stub.

News: GDELT DOC 2.0 API (free, no key; at most one request per 5 s, else HTTP 429).
  https://api.gdeltproject.org/api/v2/doc/doc?query=...&mode=artlist&format=json
      &startdatetime=YYYYMMDDHHMMSS&enddatetime=YYYYMMDDHHMMSS&maxrecords=25

LLM: Google Gemini API via the `google-genai` SDK (free tier, key from
aistudio.google.com). If GEMINI_API_KEY is not set (or the call fails, is
blocked, or comes back empty) we fall back to a plain template, so the
dashboard always renders.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

import httpx
import pandas as pd

from .config import GEMINI_MODEL
from .detect import Spike

GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_MIN_INTERVAL = 5.5  # GDELT answers 429 to more than one request per 5 s
_gdelt_last_call = 0.0
# FastAPI runs sync routes in a threadpool, so several spike cards can ask for headlines at once;
# without the lock they all see the same `_gdelt_last_call`, fire together and all get 429.
_gdelt_lock = threading.Lock()


def _gdelt_get(params: dict) -> httpx.Response:
    """GET GDELT no faster than its rate limit allows (serialised across threads), retrying once on a 429."""
    global _gdelt_last_call
    with _gdelt_lock:
        for attempt in range(2):
            wait = _gdelt_last_call + GDELT_MIN_INTERVAL - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            r = httpx.get(GDELT, params=params, timeout=20)
            _gdelt_last_call = time.monotonic()
            if r.status_code != 429:
                break
    return r


def headlines(query: str, start: pd.Timestamp, end: pd.Timestamp, limit: int = 15) -> list[dict]:
    """Articles matching `query` published in [start, end]. Returns [{title, url, domain, seendate}]."""
    fmt = "%Y%m%d%H%M%S"
    params = {
        "query": query, "mode": "artlist", "format": "json", "sort": "datedesc",
        "startdatetime": start.strftime(fmt), "enddatetime": end.strftime(fmt), "maxrecords": limit,
    }
    try:
        r = _gdelt_get(params)
        r.raise_for_status()
        arts = r.json().get("articles", [])
    except (httpx.HTTPError, ValueError):
        return []
    return [{k: a.get(k) for k in ("title", "url", "domain", "seendate")} for a in arts]


def news_window(spike: Spike, before_min: int = 60, after_min: int = 30) -> tuple[pd.Timestamp, pd.Timestamp]:
    return spike.start - pd.Timedelta(minutes=before_min), spike.peak + pd.Timedelta(minutes=after_min)


PROMPT = """You are writing one line on an alert card for a credit analyst.

A prediction market just moved:
  Market: {market_name}
  Move: {headline} ({kind}, direction {direction})
  Window: {start} to {peak} UTC
  Context for this event type: {direction_note}

Headlines published around that window (may be empty or noisy):
{headlines}

Write a detailed explanation (a short paragraph of 4-6 sentences) of why the odds moved:
1. The most likely catalyst, citing specific headlines (title and source) where they fit.
2. How the shape of the move ({kind}) supports that reading, e.g. abrupt jump vs. slow drift, and its size.
3. What it implies for the underlying event and for credit-sensitive exposure.
If none of the headlines plausibly explain it, say the cause is unclear from news and give the most plausible hypotheses, clearly labelled as hypotheses. Do not invent facts."""


def _generate(prompt: str) -> str | None:
    """One Gemini call. None without a key or when the reply was blocked/empty; raises on API errors."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    from google import genai

    client = genai.Client(api_key=key)
    resp = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    # `text` is None when the response was blocked or has no text part.
    return (resp.text or "").strip() or None


def _lines(heads: list[dict]) -> str:
    return "\n".join(f"- [{h.get('seendate')}] {h.get('title')} ({h.get('domain')})" for h in heads) or "(none found)"


def explain(spike: Spike, market_name: str, heads: list[dict], direction_note: str = "") -> str:
    """Macro alert card: a short paragraph on why the odds moved. Gemini if configured, else a template."""
    try:
        prompt = PROMPT.format(
            market_name=market_name, headline=spike.headline(), kind=spike.kind, direction=spike.direction,
            start=spike.start, peak=spike.peak, direction_note=direction_note, headlines=_lines(heads),
        )
        return _generate(prompt) or _fallback(heads)
    except Exception as e:  # keep the demo alive whatever happens
        return _fallback(heads, note=f"(explainer error: {type(e).__name__}: {str(e)[:200]})")


# ---- stocks -------------------------------------------------------------------------------

THEMES = ("AI model release", "capex", "chip export rules", "earnings", "macro", "regulation", "product", "other")
SENTIMENTS = ("positive", "negative", "mixed")
DIRECTIONS = ("bullish", "bearish", "mixed")
AI_CLIMATE_QUERY = '("artificial intelligence" OR OpenAI OR Nvidia OR Anthropic OR "AI model") AND (stocks OR shares OR Nasdaq)'
CLIMATE_HOURS = 72

STOCK_PROMPT = """You are annotating one price spike on a stock dashboard.

Stock: {name} ({ticker})
Move: {headline} ({kind}, direction {direction})
Window: {start} to {peak} UTC

Headlines published around that window (may be empty or noisy):
{headlines}

Reply with JSON only, no prose, exactly these keys:
{{"why": "<2-3 sentences on the most likely catalyst, citing headline titles and sources where they fit; if none fit, say the cause is unclear from news and give the most plausible hypothesis, labelled as such>",
 "theme": "<one of: {themes}>",
 "ai_related": <true if the catalyst is about AI models, AI demand, AI capex or AI chips, else false>,
 "stock_sentiment": "<positive | negative | mixed: what the catalyst means for this stock>"}}
Do not invent facts."""

CLIMATE_PROMPT = """You are summarising the AI news climate for tech-stock investors.

Headlines from the last {hours} hours about AI and the stock market:
{headlines}

Reply with JSON only, no prose, exactly these keys:
{{"summary": "<2 sentences on what is driving AI-related tech stocks right now>",
 "themes": [{{"theme": "<one of: {themes}>", "direction": "<bullish | bearish | mixed: for AI-exposed tech stocks>", "weight": <0..1, how much of the news is about this>}}]}}
List at most four themes. Do not invent facts."""


def _json_reply(text: str | None) -> dict | None:
    """Parse a JSON object out of a model reply, tolerating ``` fences and surrounding prose."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def _one_of(value, allowed: tuple[str, ...], default: str) -> str:
    v = str(value or "").strip().lower()
    return next((a for a in allowed if a.lower() == v), default)


def _stock_fallback(spike: Spike, heads: list[dict]) -> dict:
    return {"why": _fallback(heads), "theme": "other", "ai_related": False,
            "stock_sentiment": "positive" if spike.direction == "up" else "negative"}


def explain_stock(spike: Spike, company: dict, heads: list[dict]) -> dict:
    """{why, theme, ai_related, stock_sentiment} for one stock spike; a template on any failure."""
    try:
        prompt = STOCK_PROMPT.format(
            name=company.get("name"), ticker=company.get("ticker"), headline=spike.headline(), kind=spike.kind,
            direction=spike.direction, start=spike.start, peak=spike.peak, headlines=_lines(heads), themes=", ".join(THEMES),
        )
        obj = _json_reply(_generate(prompt))
    except Exception:
        obj = None
    if not obj or not str(obj.get("why") or "").strip():
        return _stock_fallback(spike, heads)
    default = "positive" if spike.direction == "up" else "negative"   # an unknown label falls back to the move's sign
    return {
        "why": str(obj["why"]).strip(),
        "theme": _one_of(obj.get("theme"), THEMES, "other"),
        "ai_related": str(obj.get("ai_related")).strip().lower() in ("true", "yes", "1"),
        "stock_sentiment": _one_of(obj.get("stock_sentiment"), SENTIMENTS, default),
    }


def climate(heads: list[dict], hours: int = CLIMATE_HOURS) -> dict:
    """{summary, themes: [{theme, direction, weight}]} over the recent AI headlines; empty themes without a model."""
    n = len(heads)
    fallback = {"summary": f"{n} AI headline{'s' if n != 1 else ''} in the last {hours} h; no model configured to summarise them.",
                "themes": []}
    try:
        obj = _json_reply(_generate(CLIMATE_PROMPT.format(hours=hours, headlines=_lines(heads), themes=", ".join(THEMES))))
    except Exception:
        obj = None
    if not obj or not str(obj.get("summary") or "").strip():
        return fallback
    themes = []
    for t in obj.get("themes") or []:
        if not isinstance(t, dict):
            continue
        theme = _one_of(t.get("theme"), THEMES, "")
        if not theme:
            continue
        try:
            weight = min(1.0, max(0.0, float(t.get("weight", 0))))
        except (TypeError, ValueError):
            weight = 0.0
        themes.append({"theme": theme, "direction": _one_of(t.get("direction"), DIRECTIONS, "mixed"), "weight": weight})
    return {"summary": str(obj["summary"]).strip(), "themes": themes}


def _fallback(heads: list[dict], note: str = "") -> str:
    if heads:
        return f"Likely related: \"{heads[0]['title']}\" ({heads[0].get('domain')}). {note}".strip()
    return f"Cause unclear from news so far. {note}".strip()
