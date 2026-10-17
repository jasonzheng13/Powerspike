# Powerspike

**Every spike in a tech stock, the news behind it, and whether the betting market moved first.**

Type a ticker. Powerspike pulls the last 7 or 30 days of one-minute prices, marks every move that
was big, backed by volume and held, reads the news around each one, and checks whether the
Polymarket contract on that company (for example *"Will Nvidia be the largest company by market cap
on Dec 31?"*) repriced before or after the stock did. Then it says whether the current AI news
climate reads bullish or bearish for that stock, and shows exactly what that read rests on.

Started at Hack Knight 2026 (*Prediction Markets as a Financial Signal*) as a macro-odds spike
detector; pivoted afterwards to tech stocks and the AI news cycle, which is where the odds and the
prices actually meet. The spike detector and the lead/lag engine are the same code, pointed at stocks.

> **A spike is a move that is big, backed by real money, and sticks.**

## Quick start

```bash
# backend (Python: ingest, detector, explain, outlook, HTTP API)
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # add GEMINI_API_KEY (free tier); SEC_USER_AGENT is only for the macro scripts
cd backend && pytest            # 130+ tests on synthetic data, no network
uvicorn powerspike.api:app --reload --port 8000

# frontend (TypeScript: React + Vite), in a second terminal
cd frontend && npm install
npm run dev                     # http://localhost:5173, proxies /api to :8000
```

For a single-process demo, `npm run build` writes `frontend/dist` and the backend serves it at
http://localhost:8000/. `?t=NVDA&days=30` in the URL picks a stock.

**First load is slow and the second is instant.** Bars come from Yahoo on demand (a month of
one-minute bars takes a few seconds), and every spike costs one request to the GDELT news index,
which allows roughly one request per 15 seconds. Warm the server before a demo:

```bash
cd backend
python scripts/warm_stocks.py NVDA TSLA AAPL MSFT --api http://localhost:8000
```

Without `GEMINI_API_KEY` everything still renders: explanations fall back to the first headline,
the climate to a count, the outlook prose to a template. The label never comes from the model.

## What you see

- **Chart.** Price with every spike marked (▲ up, ▼ down, the move's span shaded), volume, and the
  company's contract odds as a third row on the same time axis. Nights and weekends are cut out.
- **Outlook.** One word, *bullish* / *bearish* / *neutral*, a confidence bar, two sentences, and the
  drivers it was computed from. Always with its disclaimer.
- **Spikes, newest first.** When, how big, the news-based explanation tagged by theme (AI model
  release, capex, chip export rules, earnings, macro, regulation, product), an *AI news* badge, and
  the timing chip: *contract moved 29 min before the stock*, *stock moved first*, *contract did not
  move*, or *no contract for this company*.
- **AI news, last 72 hours.** The headlines summarised into themes with a direction for AI stocks.

Any ticker Yahoo knows works. The contract overlay and the timing chips need a contract in
`backend/config/companies.yaml` (NVDA, AAPL, MSFT, TSLA, GOOGL, AMD on 2026-10-17; Amazon's and
Meta's company contracts are pinned near zero or do not exist, so they show *no contract*). Kalshi
has no single-stock contracts, so every contract is Polymarket.

## How it works

```
yfinance ───► stocks.ensure_bars ─► bars cache (DuckDB or Tiger) ─► detect (log space) ─┬─► explain_stock (GDELT + Gemini) ─┐
Polymarket ─► stocks.ensure_contract ───────────────────────────────────────────────────┼─► react_contract (lead/lag)      ┼─► outlook ─► api ─► frontend
GDELT ──────► climate (72 h of AI headlines, Gemini themes) ────────────────────────────┘                                   │
```

### The detector (`detect.py`)

Runs on one-minute session bars (09:30–16:00 ET), in **log price**. The same code scores
prediction-market odds in log-odds; `DetectorParams.space` picks the transform.

| Test | How | Why |
|---|---|---|
| **Big?** | Log price, smoothed with a 5-bar rolling median *within each session*. Change over a 15-minute window, scored as a **robust z**: `(Δ − rolling median) / (1.4826 × rolling MAD)` over the past 4 hours, floored at 0.2% | Median/MAD aren't distorted by earlier spikes; the smoothing means a stray print can't be either end of a move; the floor keeps a quiet ticker from firing on noise |
| **Overnight?** | A window that reaches back across the open measures the overnight gap, which is about six times a 15-minute move on large caps. Its z is divided by `gap_stretch` (4) | Without this, every open was a "spike". With the measured 6, no gap in the sample month fired; 4 keeps the month's two largest gaps (+2.9%, +4.3%) |
| **Real money?** | Window volume ÷ its usual level (floored at $100k of shares). No excess volume earns 0, 2× or more earns full credit | Filters a drift on a thin book |
| **Sticks?** | Share of the move still in place 30 minutes later | Filters prints that revert |

`score = |robust z| × volume confirmation × persistence`; a spike when `score ≥ 4`, with a 60-minute
cooldown. Its `start` is the bar before the price had covered 20% of the first over-threshold
window's change, so the onset is not biased by where the score peaked. Calibrated 2026-10-17 on
30 days of NVDA, TSLA, AAPL and MSFT: 5, 12, 3 and 7 spikes, every one a 1–4% move on 2–40× volume
(`scripts/calibrate_stocks.py --show`). Drift alerts (CUSUM) exist for odds series but are off for
stocks: on session-only bars they alarm on routine overnight gaps.

### Lead/lag (`leadlag.py`)

For each stock spike, the company's contract is scored in log-odds with the same robust z, with no
session or gap logic (odds trade around the clock). The search window is 60 minutes either side of
the spike's onset; the first bar whose |z| clears 3.5 is the contract's reaction, and the lag is
wall-clock minutes from the stock's onset (negative: the contract moved first). Verdicts: *contract
led*, *stock led*, *concurrent* (within 2 minutes), *contract quiet*, *no data*. A contract pinned
near 0% or 100% clips to a constant in log-odds and always reads *quiet*; `companies.yaml` lists
such legs second or not at all.

### Outlook (`outlook.py`)

Arithmetic, with the model only writing prose:

1. **Sensitivity.** Per news theme, the score-weighted mean signed return of this stock's spikes
   tagged with it. "NVDA moved −2.1% on chip-export-rule spikes."
2. **Climate.** The last 72 hours of AI headlines, summarised by Gemini into themes with a
   direction for AI-exposed stocks and a weight.
3. **Contract.** The 7-day odds trend (signed so + is good for the stock), damped to half when the
   contract never led the stock at a spike.

`score = Σ climate direction × weight × |sensitivity[theme]| + contract term`. Label by sign
outside a ±0.5% band; confidence from the size of the total and how many spikes the sensitivities
rest on. Themes in the climate that this stock has no spike for are listed as unscored.

**Limits, stated plainly.** The sensitivities rest on a handful of spikes per month. Themes are
labelled by a language model from whatever GDELT returns for a four-hour window, which is often
nothing. The contracts are thin this month (a few thousand shares on the price ladders). Nothing
here is investment advice, and the card says so.

## Repo layout

```
backend/
  config/companies.yaml       ticker → name, sector, news query, Polymarket contracts (YES token ids)
  config/markets.yaml         macro markets (the original hackathon build; scripts still use them)
  config/exposure.yaml        event type → ETFs (macro lead/lag)
  powerspike/
    api.py                    FastAPI: /api/companies, /api/stocks/{t}, /spikes, /explain, /outlook, /api/climate
    stocks.py                 on-demand bars and contract prices with freshness tokens
    detect.py                 spike detector (log price for stocks, log-odds for markets)
    leadlag.py                react_contract (stock vs contract) and the macro ETF lead/lag
    explain.py                GDELT headlines, explain_stock (JSON), climate, the Gemini seam
    outlook.py                bullish / bearish / neutral, pure and tested
    ingest/                   equities.py (yfinance, NYSE sessions), polymarket.py, kalshi.py, cache.py, pg.py
    validate.py, synthetic.py macro validation harness; synthetic series for tests
  scripts/                    calibrate_stocks, warm_stocks, find_markets (--event-slug), pull_data, run_leadlag, run_validation
  tests/                      detector, stocks, lead/lag, explain, outlook, API; no network
frontend/                     React 19 + TypeScript + Vite + Plotly
  src/App.tsx                 search state, fetch order (cards first, outlook last)
  src/components/             SearchBar, StockChart, OutlookCard, SpikeCard, ClimatePanel
  src/lib/                    format, url, outlook helpers (vitest), theme tokens
docs/superpowers/specs/       the pivot's design note
```

## Data sources

- **yfinance:** one-minute bars for the last ~30 days (5-minute for ~60), seven days per request.
- **Polymarket:** Gamma `events?slug=` for a contract's legs and YES token ids
  (`scripts/find_markets.py --event-slug <slug>`); CLOB `prices-history` for one-minute odds, in
  14-day chunks. Volume is not needed for the stock lead/lag, so contracts are price only.
- **GDELT DOC 2.0:** `mode=artlist&format=json`. OR'd terms must sit in parentheses, adjacent
  groups are ANDed, `sourcelang:english` keeps the list readable, and live it refuses requests
  closer than ~15 seconds apart, so one request runs at a time.
- **Gemini** (`gemini-flash-lite-latest`, free tier): explanations as JSON, the climate summary,
  and two sentences of outlook prose. Every call falls back to a template.
