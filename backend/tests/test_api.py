"""API contract tests on the synthetic market. No network: explain/expose are stubbed."""

import pytest
from fastapi.testclient import TestClient

from powerspike import api


@pytest.fixture
def client(tmp_path, monkeypatch):
    real_connect = api.cache.connect
    monkeypatch.setattr(api.cache, "connect", lambda path=None: real_connect(tmp_path / "c.duckdb"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(api, "headlines", lambda q, s, e: [{"title": "Powell hints at cut", "url": "u", "domain": "d", "seendate": "x"}])
    monkeypatch.setattr(api.expose, "edgar_companies", lambda event_type, **kw: [{"company": "ACME", "filings": 3}])
    api._load.cache_clear()
    api._explain_cached.cache_clear()
    api._exposed_cached.cache_clear()
    api._load_equity.cache_clear()
    api._leadlag_cached.cache_clear()
    return TestClient(api.app)


SYN = api.SYNTHETIC["id"]


def test_markets_always_include_synthetic(client):
    ids = [m["id"] for m in client.get("/api/markets").json()["markets"]]
    assert ids == [SYN]


def test_series_shape(client):
    r = client.get(f"/api/markets/{SYN}/series").json()
    assert len(r["ts"]) == len(r["price"]) == len(r["volume"]) == 1440
    assert r["ts"][0].endswith("+00:00")
    assert all(0 <= p <= 1 for p in r["price"])


def test_unknown_market_404(client):
    assert client.get("/api/markets/nope/series").status_code == 404


def test_alerts_fire_on_jump_and_drift_with_confirmed_at(client):
    r = client.get(f"/api/markets/{SYN}/alerts").json()
    kinds = {a["kind"] for a in r["alerts"]}
    assert kinds == {"jump", "drift"}
    assert r["params"]["baseline"] == 240
    for a in r["alerts"]:
        assert a["confirmed_at"] >= a["peak"] >= a["start"]
        assert a["headline"] and a["direction"] in ("up", "down")
        assert a["volume_ratio"] is None or isinstance(a["volume_ratio"], float)


def test_alert_params_are_honoured(client):
    strict = client.get(f"/api/markets/{SYN}/alerts", params={"score_threshold": 1000}).json()
    assert [a for a in strict["alerts"] if a["kind"] == "jump"] == []


def test_explain_round_trips_a_spike_and_falls_back_without_key(client):
    spike = client.get(f"/api/markets/{SYN}/alerts").json()["alerts"][0]
    r = client.post("/api/explain", json={"market_id": SYN, "spike": spike, "query": "Powell"}).json()
    assert "Powell hints at cut" in r["why"]
    assert r["headlines"][0]["title"] == "Powell hints at cut"


def test_exposure(client):
    r = client.get("/api/exposure/fed_rates").json()
    assert "TLT" in r["etfs"] and r["companies"][0]["company"] == "ACME"
    assert r["expected"]["TLT"] == 1
    assert client.get("/api/exposure/nope").status_code == 404


def test_equity_series_for_synthetic_market(client):
    r = client.get(f"/api/markets/{SYN}/equities/TLT/series").json()
    assert r["ticker"] == "TLT" and r["market_id"] == SYN
    assert len(r["ts"]) == len(r["price"]) == len(r["volume"]) == 2 * 390
    assert r["ts"][0].endswith("+00:00")


def test_equity_series_404_when_not_cached(client):
    assert client.get(f"/api/markets/{SYN}/equities/ZZZ/series").status_code == 404
    assert client.get("/api/markets/nope/equities/TLT/series").status_code == 404


def test_leadlag_on_the_synthetic_jump(client):
    spike = [a for a in client.get(f"/api/markets/{SYN}/alerts").json()["alerts"] if a["kind"] == "jump"][0]
    r = client.post("/api/leadlag", json={"market_id": SYN, "spike": spike}).json()
    assert r["verdict"] == "market led" and r["median_lag"] > 0
    assert [x["ticker"] for x in r["reactions"]] == ["TLT", "IEF", "HYG", "LQD", "KRE", "XLF"]
    assert r["confirmed_at"] >= spike["confirmed_at"]
    tlt = r["reactions"][0]
    assert tlt["consistent"] is True and 8 <= tlt["lag_min"] <= 12 and tlt["after_hours"] is False
    assert r["reactions"][-1]["lag_min"] is None and r["reactions"][-1]["status"] == "quiet"   # XLF stays flat
