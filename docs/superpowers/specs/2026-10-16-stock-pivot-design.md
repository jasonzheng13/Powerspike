# Powerspike stock pivot: design

Date: 2026-10-16. Status: built on branch `stock-pivot` (2026-10-17).

## Intent

The hackathon (Hack Knight 2026, *Prediction Markets as a Financial Signal*) is over; this is a
portfolio piece. The macro version (Fed/CPI odds spiking, credit ETFs reacting) is replaced by a
product a judge or recruiter understands in one sentence: **type a tech ticker, see every spike in
its last 7/30 days, learn what news caused each one, see whether the Polymarket contract about that
company moved before or after the stock, and get an honest bullish/bearish read for right now,
driven by how this stock has reacted to AI news before.** The winning team's demo was a radial
"related stocks" graph; ours answers *when* and *why*, with real prices and real betting odds.

## Decisions (the user's)

- Lead/lag means the stock versus the Polymarket contract about that company, at each stock spike.
- Any ticker can be searched; the contract overlay appears only for configured companies.
- The old macro UI is replaced. Backend macro modules, `markets.yaml`, scripts and tests stay.

## Goals, non-goals, anti-goals

- Goals: real prices and real odds, timing in minutes, every verdict traceable to the numbers it
  rests on, a demo that renders without any API key.
- Non-goals: a replay clock (no live-honesty constraint any more), trading signals, portfolio
  tracking, more than one stock on screen.
- Anti-goals: a bullish/bearish label that comes from a language model; a "related stocks" graph;
  a dual-axis chart.

## Screen

Search bar (ticker, suggestions from `companies.yaml`, 7d/30d toggle, `?t=NVDA&days=30` in the
URL). Below it the **StockChart**: 1-minute price with session gaps cut out, volume, and the
company's contract odds as a third row on the same time axis. Click a spike to select it. Beside the
chart an **OutlookCard** (bullish / bearish / neutral in one loud word, confidence bar, two sentences,
drivers, disclaimer) and under it an **AI climate** panel (last 72 h of AI headlines summarised into
themes). Under the chart the **SpikeCard** list, newest first: headline
(`$182.10 → $171.40 across the open (6.2σ, 4.0× volume)`), the news-based "why", a theme chip, an
"AI news" badge, and a timing chip ("contract moved 23 min before the stock", "stock moved first,
contract followed 10 min later", "contract and stock moved together", "contract did not move",
"no contract data for this window", "no contract for this company").

## Verdict method

Sensitivity per theme = score-weighted mean signed log return of the stock's spikes tagged with that
theme. Climate themes (direction, weight) from Gemini over the 72 h AI headlines. News term =
Σ climate_dir × weight × |sensitivity[theme]|. Contract term = tanh(7-day odds trend × event_sign)
damped by how often the contract led the stock. Extra driver when the contract led historically, has
moved ≥2 pts in 24 h, and the stock has not moved yet. Label by sign with a neutral band; confidence
scaled by how many spikes exist. Gemini writes prose from the drivers; the label never comes from
the LLM. Always a disclaimer.

## Venue facts (verified 2026-10-16/17)

Polymarket `largest-company-end-of-december-2026` legs are liquid (NVDA 70%, 1.4M shares; AAPL 28%,
1.2M; MSFT 0.35%, pinned); monthly/weekly price ladders are thin this month; `which-company-has-the-
best-ai-model-end-of-october` has 2.6M. Kalshi has no single-stock price contracts, so contracts are
Polymarket only. GDELT needs parenthesised OR groups, has no AND keyword, and refuses requests closer
than ~15 s apart despite documenting 5 s.

## Left to the builder (and how it was settled)

- Chart layout: three rows, one time axis, New York wall-clock x values with session rangebreaks.
- Detector in log space: per-session smoothing and a gap stretch (measured ~6×, set to 4);
  jump start located inside the first over-threshold window; jumps only for stocks.
- Contract pulls are price-only; explanations are memoised on the spike's identity and the explain
  route explains the server's own copy; the outlook is requested after the cards.
