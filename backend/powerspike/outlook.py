"""Outlook: is this stock bullish or bearish right now, and why? Arithmetic, with Gemini only for prose.

Three inputs, all of which the dashboard shows:
  1. **Sensitivity**: how this stock moved on each AI-news theme before, from its own spikes
     (score-weighted mean signed log return per theme, `sensitivity`).
  2. **Climate**: what the last ~72 h of AI headlines are about (`explain.climate`: theme,
     direction for AI-exposed tech, weight).
  3. **Contract**: the company's prediction-market odds, as the 7-day trend (× event_sign) and
     whether that contract led the stock at past spikes.

`score` combines them: for each climate theme, direction × weight × |sensitivity| (the climate
says which way the news cuts, the stock's history says how much it cares); the contract term is a
bounded tanh of the trend, damped to half when the contract never led the stock. The label is the
sign of the total outside a neutral band; confidence grows with the size of the total and with how
many spikes the sensitivities rest on. The label never comes from the LLM; `prose` only narrates
the drivers. Every result ships with `DISCLAIMER`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import explain
from .detect import Spike

DISCLAIMER = ("Not investment advice. This read is arithmetic over a handful of past spikes, model-labelled "
              "news themes and thin prediction-market odds; it can be wrong and it ignores everything else.")


@dataclass
class OutlookParams:
    neutral_band: float = 0.005            # |score| below this is "neutral" (log-return units, ~0.5%)
    full_confidence_at: float = 0.03       # |score| at which the size part of confidence saturates (~3%)
    min_spikes_for_full_coverage: int = 4  # spikes needed before the sensitivities count fully
    contract_scale: float = 0.02           # the contract term's cap, in log-return units
    contract_trend_pts_for_full: float = 10.0  # a 7-day odds move of this many points is "a lot"
    pending_move_pts: float = 2.0          # a 24 h odds move at least this big, with the stock quiet, is flagged
    led_share_for_pending: float = 0.5


@dataclass
class Driver:
    kind: str      # "theme" | "contract" | "coverage"
    text: str
    weight: float  # signed contribution to the score (0 for coverage notes)


@dataclass
class Outlook:
    label: str     # "bullish" | "bearish" | "neutral"
    confidence: float
    drivers: list[Driver] = field(default_factory=list)
    n_spikes: int = 0
    score: float = 0.0


DIRECTION_SIGN = {"bullish": 1, "bearish": -1, "mixed": 0}


def spike_return(spike: Spike) -> float:
    """Signed log return of the spike's move."""
    return float(math.log(spike.p_after / spike.p_before))


def sensitivity(pairs: list[tuple[Spike, dict]]) -> dict[str, float]:
    """Per theme: the score-weighted mean signed log return of the stock's spikes tagged with it."""
    num: dict[str, float] = {}
    den: dict[str, float] = {}
    for spike, expl in pairs:
        theme = expl.get("theme") or "other"
        w = max(float(spike.score), 1e-9)
        num[theme] = num.get(theme, 0.0) + w * spike_return(spike)
        den[theme] = den.get(theme, 0.0) + w
    return {t: num[t] / den[t] for t in num}


def contract_signal(df: pd.DataFrame, event_sign: int, now: pd.Timestamp) -> dict | None:
    """The contract's 7-day trend and 24 h move in probability points, signed so + is good for the stock."""
    if df is None or df.empty:
        return None
    price = df["price"].astype(float).dropna()
    if price.empty:
        return None
    last = float(price.iloc[-1])

    def at(delta: pd.Timedelta) -> float:
        before = price[price.index <= now - delta]
        return float(before.iloc[-1]) if not before.empty else float(price.iloc[0])

    sign = int(event_sign) or 1
    return {"trend_7d_pts": sign * (last - at(pd.Timedelta(days=7))) * 100,
            "move_24h_pts": sign * (last - at(pd.Timedelta(hours=24))) * 100}


def score(sens: dict[str, float], climate_themes: list[dict], contract_trend: float | None,
          contract_led_share: float | None, *, contract_move_24h: float | None = None,
          stock_quiet_24h: bool = True, n_spikes: int = 0, reacted_n: int | None = None,
          params: OutlookParams | None = None) -> Outlook:
    p = params or OutlookParams()
    drivers: list[Driver] = []
    total = 0.0
    unscored: list[str] = []
    for t in climate_themes:
        theme, d, w = t.get("theme"), DIRECTION_SIGN.get(t.get("direction"), 0), float(t.get("weight") or 0)
        if theme not in sens:
            unscored.append(str(theme))
            continue
        contribution = d * w * abs(sens[theme])
        total += contribution
        drivers.append(Driver("theme", f"Historically moved {sens[theme]:+.1%} on {theme} spikes; the current {theme} "
                                       f"news reads {t.get('direction')} (weight {w:.1f}).", contribution))
    if unscored:
        names = ", ".join(unscored[:-1]) + (" or " if len(unscored) > 1 else "") + unscored[-1]
        drivers.append(Driver("coverage", f"No past spike was tagged {names}, so this week's news on that is unscored.", 0.0))
    if contract_trend is not None:
        share = 0.0 if contract_led_share is None else float(contract_led_share)
        contribution = p.contract_scale * math.tanh(contract_trend / p.contract_trend_pts_for_full) * (0.5 + 0.5 * share)
        total += contribution
        if reacted_n is None:
            led_text = f"it led the stock in {share:.0%} of the spikes where it moved"
        elif reacted_n == 0:
            led_text = f"it did not move on any of the {n_spikes} spikes"
        else:
            led_text = f"it moved first on {round(share * reacted_n)} of the {n_spikes} spikes, and moved at all on {reacted_n}"
        drivers.append(Driver("contract", f"The company's contract moved {contract_trend:+.0f} pts over 7 days ({led_text}).",
                              contribution))
        if (share >= p.led_share_for_pending and contract_move_24h is not None
                and abs(contract_move_24h) >= p.pending_move_pts and stock_quiet_24h):
            drivers.append(Driver("contract", f"The contract has moved {contract_move_24h:+.0f} pts in the last 24 h "
                                              f"without a stock move yet, and {led_text}.", 0.0))
    if total > p.neutral_band:
        label = "bullish"
    elif total < -p.neutral_band:
        label = "bearish"
    else:
        label = "neutral"
    size = min(1.0, abs(total) / p.full_confidence_at)
    coverage = min(1.0, n_spikes / p.min_spikes_for_full_coverage) if p.min_spikes_for_full_coverage else 1.0
    drivers.sort(key=lambda d: -abs(d.weight))
    return Outlook(label, float(size * coverage), drivers, int(n_spikes), float(total))


PROSE_PROMPT = """You are writing two sentences for a stock dashboard's outlook card.

Stock: {name}
Computed label: {label} (confidence {confidence:.0%}, from {n_spikes} past spikes)
Drivers, strongest first:
{drivers}

Write exactly two plain sentences explaining the label from these drivers. Do not add new facts,
numbers or advice, and do not contradict the label."""


def prose(o: Outlook, name: str) -> str:
    """Two sentences from the drivers: Gemini when configured, else a deterministic template."""
    lines = "\n".join(f"- {d.text}" for d in o.drivers) or "- No drivers: nothing in the climate matches this stock's history."
    try:
        text = explain._generate(PROSE_PROMPT.format(name=name, label=o.label, confidence=o.confidence,
                                                     n_spikes=o.n_spikes, drivers=lines))
    except Exception:
        text = None
    if text:
        return text
    top = [d.text for d in o.drivers[:2]]
    if not top:
        return f"{name} reads {o.label}: nothing in the current AI news matches a theme this stock has reacted to before."
    return f"{name} reads {o.label}. " + " ".join(top)
