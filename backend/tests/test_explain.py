"""Stock explanations and the AI-news climate: JSON parsing, fallbacks, and the GDELT throttle. No network."""

import threading
import time

import httpx
import pandas as pd
import pytest

from powerspike import explain
from powerspike.detect import Spike

T = pd.Timestamp("2025-12-10 15:15", tz="UTC")
SPIKE = Spike("equity:NVDA", "jump", T, T + pd.Timedelta(minutes=8), T + pd.Timedelta(minutes=38),
              180.0, 171.0, 6.0, 4.0, 1.0, 20.0, "usd")
COMPANY = {"ticker": "NVDA", "name": "Nvidia", "news_query": '"Nvidia" OR NVDA'}
HEADS = [{"title": "Cheap open-weights model rattles chip stocks", "url": "u", "domain": "reuters.com", "seendate": "20251210T150000Z"}]


@pytest.fixture
def gemini(monkeypatch):
    """Stub the single Gemini seam with a fixed reply (None = no key / blocked)."""
    def install(reply):
        monkeypatch.setattr(explain, "_generate", lambda prompt: reply)
    return install


def test_explain_stock_falls_back_without_a_model(gemini):
    gemini(None)
    out = explain.explain_stock(SPIKE, COMPANY, HEADS)
    assert out == {"why": explain._fallback(HEADS), "theme": "other", "ai_related": False, "stock_sentiment": "negative"}
    assert explain.explain_stock(SPIKE, COMPANY, [])["why"].startswith("Cause unclear")


def test_explain_stock_parses_json_and_fenced_json(gemini):
    reply = '{"why": "A cheap model cut GPU demand hopes.", "theme": "AI model release", "ai_related": true, "stock_sentiment": "negative"}'
    gemini(reply)
    assert explain.explain_stock(SPIKE, COMPANY, HEADS)["theme"] == "AI model release"
    gemini("```json\n" + reply + "\n```")
    out = explain.explain_stock(SPIKE, COMPANY, HEADS)
    assert out["ai_related"] is True and out["why"].startswith("A cheap model")


def test_explain_stock_normalises_bad_fields(gemini):
    gemini('{"why": "x", "theme": "Vibes", "ai_related": "yes", "stock_sentiment": "very bad"}')
    out = explain.explain_stock(SPIKE, COMPANY, HEADS)
    assert out["theme"] == "other" and out["ai_related"] is True
    assert out["stock_sentiment"] == "negative"   # unknown label: the spike is down, so the default is negative


def test_explain_stock_garbage_reply_falls_back(gemini, monkeypatch):
    gemini("I cannot help with that.")
    assert explain.explain_stock(SPIKE, COMPANY, HEADS)["theme"] == "other"

    def raiser(prompt):
        raise RuntimeError("quota")

    monkeypatch.setattr(explain, "_generate", raiser)
    assert explain.explain_stock(SPIKE, COMPANY, HEADS)["why"] == explain._fallback(HEADS)


def test_climate_parses_and_falls_back(gemini):
    gemini(None)
    c = explain.climate(HEADS)
    assert c["themes"] == [] and "1 AI headline" in c["summary"]
    gemini('{"summary": "AI capex worries dominate.", "themes": [{"theme": "capex", "direction": "bearish", "weight": 0.8},'
           ' {"theme": "nonsense", "direction": "bullish", "weight": 1}, {"theme": "earnings", "direction": "sideways", "weight": 3}]}')
    c = explain.climate(HEADS)
    assert c["summary"] == "AI capex worries dominate."
    assert c["themes"] == [{"theme": "capex", "direction": "bearish", "weight": 0.8},
                           {"theme": "earnings", "direction": "mixed", "weight": 1.0}]


def test_generate_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert explain._generate("hello") is None


def test_gdelt_calls_from_two_threads_are_spaced_by_the_interval(monkeypatch):
    monkeypatch.setattr(explain, "GDELT_MIN_INTERVAL", 0.3)
    monkeypatch.setattr(explain, "_gdelt_next_slot", 0.0)
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append(time.monotonic())
        return httpx.Response(200, json={"articles": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(explain.httpx, "get", fake_get)
    threads = [threading.Thread(target=explain.headlines, args=("q", T, T)) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    gaps = sorted(b - a for a, b in zip(sorted(seen), sorted(seen)[1:]))
    assert len(seen) == 3 and gaps[0] >= 0.29


def test_gdelt_caller_gives_up_instead_of_queueing_past_the_cap(monkeypatch):
    monkeypatch.setattr(explain, "GDELT_MIN_INTERVAL", 5.0)
    monkeypatch.setattr(explain, "GDELT_MAX_WAIT", 1.0)
    monkeypatch.setattr(explain, "_gdelt_next_slot", 0.0)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params)
        return httpx.Response(200, json={"articles": [{"title": "t", "url": "u", "domain": "d", "seendate": "s"}]}, request=httpx.Request("GET", url))

    monkeypatch.setattr(explain.httpx, "get", fake_get)
    assert explain.headlines("q", T, T) == [{"title": "t", "url": "u", "domain": "d", "seendate": "s"}]
    assert explain.headlines("q", T, T) == []        # next slot is 5 s away, more than the 1 s cap
    assert len(calls) == 1


def test_gdelt_or_queries_are_parenthesised(monkeypatch):
    monkeypatch.setattr(explain, "GDELT_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(explain, "_gdelt_next_slot", 0.0)
    sent = []

    def fake_get(url, params=None, timeout=None):
        sent.append(params["query"])
        return httpx.Response(200, json={"articles": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(explain.httpx, "get", fake_get)
    explain.headlines('"Nvidia" OR NVDA', T, T)
    explain.headlines("(Nvidia OR NVDA)", T, T)
    explain.headlines("Nvidia", T, T)
    explain.headlines('("artificial intelligence" OR OpenAI) (stocks OR shares)', T, T)
    assert sent == ['("Nvidia" OR NVDA)', "(Nvidia OR NVDA)", "Nvidia", '("artificial intelligence" OR OpenAI) (stocks OR shares)']
    assert " AND " not in explain.AI_CLIMATE_QUERY and explain.AI_CLIMATE_QUERY.startswith("(")


def test_gdelt_spacing_is_measured_from_the_previous_response(monkeypatch):
    # GDELT answers slowly; the next request must wait the interval after the previous one *returned*.
    monkeypatch.setattr(explain, "GDELT_MIN_INTERVAL", 0.3)
    monkeypatch.setattr(explain, "_gdelt_next_slot", 0.0)
    log = []

    def slow_get(url, params=None, timeout=None):
        log.append(("start", time.monotonic()))
        time.sleep(0.2)
        log.append(("end", time.monotonic()))
        return httpx.Response(200, json={"articles": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(explain.httpx, "get", slow_get)
    explain.headlines("a", T, T)
    explain.headlines("b", T, T)
    first_end, second_start = log[1][1], log[2][1]
    assert second_start - first_end >= 0.29
