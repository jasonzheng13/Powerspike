"""Outlook: the bullish/bearish label is arithmetic over the stock's own spikes, the AI climate and the contract."""

import math

import pandas as pd
import pytest

from powerspike import explain, outlook
from powerspike.detect import Spike

T = pd.Timestamp("2025-12-10 15:15", tz="UTC")


def _spike(before: float, after: float, score: float) -> Spike:
    return Spike("equity:NVDA", "jump", T, T + pd.Timedelta(minutes=8), T + pd.Timedelta(minutes=38),
                 before, after, 6.0, 4.0, 1.0, score, "usd")


def _expl(theme: str) -> dict:
    return {"why": "x", "theme": theme, "ai_related": True, "stock_sentiment": "mixed"}


def test_spike_return_is_the_signed_log_return():
    assert outlook.spike_return(_spike(100.0, 110.0, 5)) == pytest.approx(math.log(1.1))
    assert outlook.spike_return(_spike(110.0, 100.0, 5)) < 0


def test_sensitivity_is_the_score_weighted_mean_per_theme():
    pairs = [(_spike(100.0, 100.0 * math.exp(0.02), 10), _expl("capex")),
             (_spike(100.0, 100.0 * math.exp(-0.02), 30), _expl("capex")),
             (_spike(100.0, 100.0 * math.exp(0.05), 5), _expl("earnings"))]
    s = outlook.sensitivity(pairs)
    assert s["capex"] == pytest.approx(-0.01, abs=1e-6) and s["earnings"] == pytest.approx(0.05, abs=1e-6)
    assert outlook.sensitivity([]) == {}


def test_climate_direction_and_the_stocks_own_exposure_set_the_label():
    sens = {"AI model release": -0.05}            # NVDA historically fell 5% on model-release spikes
    bull = [{"theme": "AI model release", "direction": "bullish", "weight": 1.0}]
    bear = [{"theme": "AI model release", "direction": "bearish", "weight": 1.0}]
    up = outlook.score(sens, bull, None, None, n_spikes=4)
    down = outlook.score(sens, bear, None, None, n_spikes=4)
    assert up.label == "bullish" and down.label == "bearish"
    assert up.confidence == 1.0 and up.score == pytest.approx(0.05)
    assert any(d.kind == "theme" and "AI model release" in d.text and "-5.0%" in d.text for d in up.drivers)


def test_no_signal_is_neutral_with_zero_confidence():
    o = outlook.score({}, [], None, None, n_spikes=0)
    assert o.label == "neutral" and o.confidence == 0.0 and o.score == 0.0 and o.n_spikes == 0


def test_mixed_climate_and_unknown_themes_contribute_nothing_but_a_coverage_driver():
    themes = [{"theme": "capex", "direction": "mixed", "weight": 1.0}, {"theme": "macro", "direction": "bearish", "weight": 0.5}]
    o = outlook.score({"capex": 0.03}, themes, None, None, n_spikes=4)
    assert o.label == "neutral" and o.score == 0.0
    assert any(d.kind == "coverage" and "macro" in d.text for d in o.drivers)


def test_contract_trend_moves_the_score_and_is_damped_when_it_never_led():
    led = outlook.score({}, [], 10.0, 1.0, n_spikes=4)
    never = outlook.score({}, [], 10.0, 0.0, n_spikes=4)
    assert led.label == "bullish" and led.score == pytest.approx(0.02 * math.tanh(1.0))
    assert never.score == pytest.approx(led.score / 2)
    assert outlook.score({}, [], -10.0, 1.0, n_spikes=4).label == "bearish"
    assert any(d.kind == "contract" for d in led.drivers)


def test_pending_contract_move_driver_only_when_it_led_moved_and_the_stock_is_quiet():
    def has(**kw):
        o = outlook.score({}, [], 2.0, kw.pop("share"), n_spikes=4, **kw)
        return any("without a stock move" in d.text for d in o.drivers)
    assert has(share=0.6, contract_move_24h=3.0, stock_quiet_24h=True)
    assert not has(share=0.6, contract_move_24h=3.0, stock_quiet_24h=False)
    assert not has(share=0.6, contract_move_24h=1.0, stock_quiet_24h=True)
    assert not has(share=0.4, contract_move_24h=3.0, stock_quiet_24h=True)


def test_confidence_is_capped_and_scaled_by_spike_coverage():
    themes = [{"theme": "capex", "direction": "bullish", "weight": 1.0}]
    assert outlook.score({"capex": 0.5}, themes, None, None, n_spikes=10).confidence == 1.0
    assert outlook.score({"capex": 0.5}, themes, None, None, n_spikes=2).confidence == pytest.approx(0.5)
    assert outlook.score({"capex": 0.015}, themes, None, None, n_spikes=4).confidence == pytest.approx(0.5)


def test_contract_signal_trend_and_24h_move_in_points():
    idx = pd.date_range("2025-12-01", periods=8 * 1440, freq="1min", tz="UTC")
    price = pd.Series(0.50, index=idx)
    now = idx[-1]
    price[idx > now - pd.Timedelta(hours=24)] = 0.60
    df = pd.DataFrame({"price": price, "volume": float("nan")})
    sig = outlook.contract_signal(df, event_sign=1, now=now)
    assert sig == {"trend_7d_pts": pytest.approx(10.0), "move_24h_pts": pytest.approx(10.0)}
    assert outlook.contract_signal(df, event_sign=-1, now=now)["trend_7d_pts"] == pytest.approx(-10.0)
    assert outlook.contract_signal(pd.DataFrame(columns=["price", "volume"]), 1, now) is None


def test_prose_falls_back_to_a_deterministic_template(monkeypatch):
    monkeypatch.setattr(explain, "_generate", lambda prompt: None)
    themes = [{"theme": "capex", "direction": "bearish", "weight": 1.0}]
    o = outlook.score({"capex": 0.04}, themes, None, None, n_spikes=4)
    text = outlook.prose(o, "Nvidia")
    assert "Nvidia" in text and "bearish" in text and o.drivers[0].text in text
    monkeypatch.setattr(explain, "_generate", lambda prompt: "Model prose.")
    assert outlook.prose(o, "Nvidia") == "Model prose."
    assert "not investment advice" in outlook.DISCLAIMER.lower()
