import httpx
import pandas as pd
import pytest

from powerspike.ingest import polymarket, to_grid

T0 = pd.Timestamp("2026-10-01 12:00", tz="UTC")
S0 = int(T0.timestamp())


def _trade(ts, size, asset="yes", tx=None):
    return {"timestamp": ts, "size": size, "asset": asset, "side": "BUY", "proxyWallet": "w", "transactionHash": tx or f"tx{ts}{asset}{size}"}


class FakeAPI:
    """Stands in for httpx.get: serves prices-history, Gamma lookup and a trades feed with real filtering."""

    def __init__(self, prices, trades):
        self.prices, self.trades, self.trade_calls = prices, trades, []

    def __call__(self, url, params=None, timeout=None):
        if url.endswith("/prices-history"):
            body = {"history": [{"t": t, "p": p} for t, p in self.prices]}
        elif url.endswith("/markets"):
            body = [{"conditionId": "0xabc"}]
        elif url.endswith("/trades"):
            self.trade_calls.append(params)
            assert params["market"] == "0xabc"
            hits = [t for t in self.trades if params["start"] <= t["timestamp"] <= params["end"]]
            body = sorted(hits, key=lambda t: -t["timestamp"])[: params["limit"]]
        else:
            raise AssertionError(url)
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))


@pytest.fixture
def fake(monkeypatch):
    def install(prices, trades):
        api = FakeAPI(prices, trades)
        monkeypatch.setattr(polymarket.httpx, "get", api)
        return api

    return install


def test_trades_volume_pages_by_time_without_duplicates(fake, monkeypatch):
    monkeypatch.setattr(polymarket, "TRADES_PAGE", 3)
    # 7 trades; three share one second, so they straddle a page boundary.
    trades = [_trade(S0 + s, 1.0 + i) for i, s in enumerate([0, 60, 60, 60, 120, 180, 240])]
    api = fake([], trades)
    vol = polymarket.trades_volume("0xabc", T0, T0 + pd.Timedelta(hours=1))
    assert len(api.trade_calls) > 1
    assert len(vol) == 7
    assert vol.sum() == sum(t["size"] for t in trades)
    assert vol.index.is_monotonic_increasing and str(vol.index.tz) == "UTC"


def test_trades_volume_survives_a_full_page_in_one_second(fake, monkeypatch):
    monkeypatch.setattr(polymarket, "TRADES_PAGE", 2)
    trades = [_trade(S0 + 60, 1.0, tx=f"t{i}") for i in range(4)] + [_trade(S0, 5.0)]
    fake([], trades)
    vol = polymarket.trades_volume("0xabc", T0, T0 + pd.Timedelta(hours=1))
    assert vol.iloc[0] == 5.0  # loop stepped past the crowded second and reached the older trade


def test_history_grid_sums_both_outcomes_per_minute(fake):
    prices = [(S0, 0.30), (S0 + 60, 0.32), (S0 + 180, 0.40)]
    trades = [_trade(S0 + 10, 100.0), _trade(S0 + 50, 50.0, asset="no"), _trade(S0 + 200, 25.0)]
    fake(prices, trades)
    grid = to_grid(polymarket.history("yes", T0, T0 + pd.Timedelta(minutes=5)))
    assert list(grid["volume"]) == [150.0, 0.0, 0.0, 25.0]
    assert list(grid["price"]) == [0.30, 0.32, 0.32, 0.40]  # trade rows never overwrite or forward a price


def test_history_without_trades_is_zero_volume_not_volume_free(fake):
    fake([(S0, 0.5), (S0 + 60, 0.5)], [])
    grid = to_grid(polymarket.history("yes", T0, T0 + pd.Timedelta(minutes=2)))
    assert grid["volume"].notna().all() and grid["volume"].sum() == 0


def test_history_empty_prices_stays_empty(fake):
    fake([], [_trade(S0, 1.0)])
    assert polymarket.history("yes", T0, T0 + pd.Timedelta(minutes=2)).empty


def test_rate_limit_is_retried(fake, monkeypatch):
    api = fake([], [_trade(S0, 3.0)])
    calls = {"n": 0}

    def flaky(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=httpx.Request("GET", url))
        return api(url, params, timeout)

    monkeypatch.setattr(polymarket.httpx, "get", flaky)
    assert polymarket.trades_volume("0xabc", T0, T0 + pd.Timedelta(hours=1)).sum() == 3.0
