# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Powerspike is a stock dashboard: type a tech ticker, see its last 7/30 days of 1-minute bars with
every *spike* marked (big, volume-backed, held), each spike explained from the news around it and
tagged by theme, the Polymarket contract on the company overlaid with a per-spike "contract led /
stock led" verdict, and an **Outlook** card (bullish / bearish / neutral) computed from how the
stock reacted to AI-news themes before, the current 72-hour AI news climate, and the contract's
trend. It began at Hack Knight 2026 as a macro-odds spike detector (Fed, CPI) with credit-ETF
lead/lag; that code is still here (`markets.yaml`, `exposure.yaml`, `expose.py`, `validate.py`,
`scripts/pull_data.py`, `run_leadlag.py`, `run_validation.py`) and tested, but nothing in the UI or
the API uses it. `README.md` holds the pitch and the method write-ups; the pivot's design note is
`docs/superpowers/specs/2026-10-16-stock-pivot-design.md`.

## Commands

```bash
# backend — run from backend/ (the .venv lives at the repo root)
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env                      # GEMINI_API_KEY; optional GEMINI_MODEL / DD_CACHE_PATH / DD_DATABASE_URL
cd backend
pytest                                    # all tests (synthetic data only, no network)
pytest tests/test_detect.py::test_routine_overnight_gap_is_not_a_spike_but_the_same_move_intraday_is -q
uvicorn powerspike.api:app --reload --port 8000           # API; serves frontend/dist at / if built

python scripts/calibrate_stocks.py --show --contracts     # jump counts over a parameter grid on real tickers; pre-warms the bar cache
python scripts/warm_stocks.py NVDA TSLA --api http://localhost:8000   # explain every spike + outlook in a running API (slow: GDELT)
python scripts/find_markets.py --event-slug largest-company-end-of-december-2026   # a Polymarket event's legs + YES token ids
python scripts/pull_data.py --days 7      # macro markets + ETFs (the original build)
python scripts/run_leadlag.py             # macro market-vs-ETF lead/lag summary
python scripts/run_validation.py          # macro hit rate on known_events.yaml

# frontend — run from frontend/
npm install
npm run dev                               # http://localhost:5173, proxies /api -> :8000 (vite.config.ts)
npm run typecheck                         # tsc --noEmit (strict, noUncheckedIndexedAccess)
npm test                                  # vitest: src/lib/*.test.ts
npm run build                             # tsc + vite build -> frontend/dist
```

There is no linter or formatter configured and no CI. Port 8000 on this machine is sometimes held
by an older server; use another port if `uvicorn` reports the address in use.

## Architecture

```
yfinance ───► stocks.ensure_bars ──► bars cache ──► detect_jumps (EQUITY_PARAMS) ─┬─► explain_stock (GDELT + Gemini) ─┐
Polymarket ─► stocks.ensure_contract ─► bars cache ───────────────────────────────┼─► leadlag.react_contract          ┼─► outlook.score ─► api.py ─► frontend/src
GDELT ──────► explain.climate (72 h AI headlines → themes) ───────────────────────┘                                    │
```

Contracts that hold the pipeline together:

1. **The bar frame.** Every series is a DataFrame indexed by UTC timestamp with columns `price` and
   `volume`, or an empty frame. Stocks: `ingest/equities.py` turns yfinance bars into dollars and
   shares, `session_grid` grids them *per NYSE session* (09:30–16:00 ET, weekdays, holidays ignored)
   so nothing is forward-filled overnight; cached as `equity:<TICKER>`. Contracts: `polymarket.price_history`
   (price only, NaN volume; `to_grid` forward-fills on a 24/7 1-minute grid), cached under the
   `companies.yaml` id (`poly:<slug>`). Macro markets keep their `markets.yaml` ids. All three share
   the `bars(market_id, ts, price, volume)` table with an upsert on `(market_id, ts)`; `cache.connect()`
   returns Tiger Cloud when `DD_DATABASE_URL` is set, else the local DuckDB file; tests pass an explicit
   DuckDB path. yfinance serves 1-minute bars for ~30 days and 5-minute for ~60 (`fetch_plan`); nothing
   older can be pulled. Over the wire, `GET /api/stocks/{t}` sends `ts` / `price` / `volume` arrays plus
   the contract at **5-minute** points (`CONTRACT_WIRE_FREQ`); lead/lag uses the 1-minute grid.
2. **On demand, with freshness tokens.** Nothing is pulled ahead of time. `stocks.refresh(con, ticker,
   days, now)` pulls what is missing (no bars; the window's first 5 days absent, tried once per
   process) or stale (in session: last bar older than 1 h; otherwise older than the last close), and
   returns the newest bar's timestamp. `api.py` keys every `lru_cache` on that token (`_bars`,
   `_spikes`, `_reactions`, `_outlook`) so a re-pull shows up without a restart; contracts have their
   own token from `ensure_contract` (1 h staleness). An unknown ticker (yfinance returns nothing) is a
   404; a transport error is a 503. `stocks.company()` merges `companies.yaml` with a yfinance short
   name for unconfigured tickers (`company_name`, slow, cached per process).
3. **`Spike` with `unit`.** `detect.detect_jumps()` returns `Spike` objects; `unit` is `"usd"` in log
   space and `"prob"` in log-odds, and `headline()` formats accordingly (`$182.10 → $171.40 across the
   open (6.2σ, 4.0× volume)`). `confirmed_at` is still filled (peak + `hold` bars) but nothing in the
   stock UI filters on it; there is no replay clock any more. `spike_to_json`/`spike_from_json` in
   `api.py` round-trip it. **`POST /explain` takes only `{kind, peak}` as a reference** and explains
   the server's own detected copy (404 if none matches), so client fields never reach the prompt or
   the memo.

### Detector (`detect.py`)

`score = |robust z| × vol_conf × persistence`, alert at `score >= 4`, 60-bar cooldown; everything in
`DetectorParams`. Two spaces, chosen by `space`:

- `"logit"` (macro odds): log-odds, tail gate (both ends under 5% / over 95% score 0), volume floor
  `min_notional / min(p, 1-p)`, 5-bar rolling median over the whole series. Drifts (two-sided CUSUM on
  15-minute bars) are run by `detect()`.
- `"log"` (stocks, `EQUITY_PARAMS`): log price, no tail gate, floor `min_notional / price`, `min_scale`
  0.002 in log-return units (just above the measured 15-minute scale of 0.0016–0.0022). Because
  `score_series` shifts are **positional**, on session-only bars the overnight gap is one more bar, so
  (a) the 5-bar median is taken **within each session** (`gap_stretch != 1` switches this on), or the
  previous close leaks into the first windows after the open and produced spikes like "$225.13 →
  $225.43 (−6.7σ)"; and (b) a window spanning the open is scored on the gap scale: z is divided by
  `gap_stretch` (measured ~6× on NVDA/TSLA/AAPL/MSFT; set to 4 so 3%+ gaps still fire). Calibrated
  2026-10-17: 5 / 12 / 3 / 7 jumps per 30 days, all 1–4% moves on 2–40× volume
  (`scripts/calibrate_stocks.py --show`). **The API runs `detect_jumps` only for stocks**: drifts over
  session-only 15-minute bars alarm on routine gaps.
- A jump's `start` is `_onset()`: the bar before the price had covered `onset_frac` (0.2) of the first
  over-threshold window's change. `peak - window` (the old rule) could land *after* the real onset,
  which biased every lead/lag reading; `first_hot - window` lands a window too early.
- Dead zones: the lagged median/MAD needs `baseline/2` bars, so the first ~135 bars of a series
  cannot fire; the last `hold` bars cannot either.

### Lead/lag (`leadlag.py`)

`react_contract(spike, contract_df, contract_id, event_sign)` scores the contract with `contract_z`
(logit, `robust_z` over `window` bars, floored at `contract_min_scale` = 0.05 logit so a 1¢ tick on a
pinned contract is not a reaction; `min_scale` = 0.001 is in log-return units for ETFs and must not
be reused), with no session grouping and no gap. The window is `lookaround` (60) minutes either side
of `spike.start` (the search in `_first_reaction` is shared with the ETF path, which already looked
both ways for an in-session onset); lag is wall-clock minutes from the onset to the first hot bar;
`expected_move = stock direction × event_sign`; verdicts "contract led" / "stock led" / "concurrent"
(±2 min) / "contract quiet" / "no data"; `confirmed_at` = last bar searched. A contract pinned near
0%/100% clips to a constant after `logit` (eps 0.005) and always reads quiet, which is why MSFT's
"largest company" leg (0.35%) is listed second in `companies.yaml`. The ETF path (`react`, `lead_lag`,
`summary`, `run_cached`) is unchanged for the macro scripts.

### Explain (`explain.py`)

- Every Gemini call goes through `_generate(prompt) -> str | None` (None without a key or when the
  reply is blocked/empty; raises on API errors). Tests stub it. Callers fall back to templates.
- `explain_stock(spike, company, heads)` asks for **JSON only** (`why`, `theme` ∈ `THEMES`,
  `ai_related`, `stock_sentiment`), parses through ``` fences, validates, and falls back to
  `_stock_fallback` (headline template, theme "other", sentiment from the move's direction).
- `climate(heads)` summarises the last `CLIMATE_HOURS` (72) of `AI_CLIMATE_QUERY` headlines into
  `{summary, themes: [{theme, direction, weight}]}`.
- **GDELT, live-tested 2026-10-17:** OR'd terms must be inside parentheses (`gdelt_query()` wraps a
  bare `a OR b`; `companies.yaml` queries are written wrapped), adjacent groups are ANDed (no `AND`
  keyword), `sourcelang:english` is appended, responses take 7–9 s, and it answers 429 to requests
  closer than ~15 s even though it says 5. `_gdelt_get` reserves spaced slots under a short lock
  (`GDELT_MIN_INTERVAL` 15 s, measured from the previous *response*), retries a 429 up to
  `GDELT_RETRIES` (3) times, logs refusals (`powerspike.explain` logger), and `headlines()` returns `[]`
  past `GDELT_MAX_WAIT` (120 s of queue) or on any error. The stock news window is
  `STOCK_NEWS_BEFORE_MIN` (240) minutes before the onset to 30 after; shorter windows came back empty.
  A cold ticker with N spikes therefore takes ~N × 15–45 s to explain: run `scripts/warm_stocks.py`
  before a demo.

### Outlook (`outlook.py`, pure, tested)

`sensitivity(pairs)` = per theme, score-weighted mean signed log return of the stock's spikes tagged
with it. `contract_signal(df, event_sign, now)` = 7-day trend and 24 h move in points, signed so +
is good for the stock. `score(sens, climate_themes, contract_trend, contract_led_share, *,
contract_move_24h, stock_quiet_24h, n_spikes, reacted_n)`: news term Σ direction × weight ×
|sensitivity| (the climate gives the sign, the stock's history the magnitude); contract term
`contract_scale × tanh(trend / 10 pts) × (0.5 + 0.5 × led_share)`; themes with no sensitivity become
one "coverage" driver; a "contract moved ≥2 pts in 24 h, stock quiet, contract led before" driver
when applicable; label by sign outside `neutral_band` (0.005); confidence = size part (saturating at
0.03) × coverage part (`n_spikes / 4`). `prose()` narrates the drivers via `_generate` with a
deterministic fallback; `DISCLAIMER` always ships. Tunables in `OutlookParams`.

### API (`api.py`) and frontend

Routes: `GET /api/companies` (companies.yaml minus token ids), `GET /api/stocks/{t}?days=7|30`
(`{ticker, name, configured, sector, as_of, ts, price, volume, contract | null}`; contract failures
are `null`, never 500), `GET /api/stocks/{t}/spikes?days` (`{params, spikes: [spike +
contract_leadlag | null]}`), `POST /api/stocks/{t}/explain?days {spike: {kind, peak}}`,
`GET /api/climate` (30-minute bucket cache), `GET /api/stocks/{t}/outlook?days` (needs every spike's
explanation: sequential GDELT calls on a miss; cached per token/ctoken/bucket). Explanations are
memoised in a bounded LRU keyed on the spike's identity (`_explain_memo`), not its JSON. `days` must
be 7 or 30 (400 otherwise). If `frontend/dist` exists at import time the API mounts it at `/`.

Frontend: `App.tsx` owns `ticker`/`days` (synced to `?t=&days=` via `lib/url.ts`), the stock and
spikes responses, the selected spike, the set of explained spike keys and the outlook. **The outlook
is fetched only after every `SpikeCard` has reported `onExplained`**, so the slow call reuses the
server memo instead of queueing behind the cards at GDELT. `StockChart` (Plotly, imperative
`Plotly.react` in a `useEffect`) draws price, volume and contract odds as **three rows on one time
axis** (never a dual axis); x values are New York wall-clock strings so `rangebreaks` can cut nights
and weekends, which means a contract's overnight repricing shows as a step at the open and the
lead/lag chip carries the exact timing. Spike markers are triangles (direction by shape as well as
color). Colors are CSS tokens read by `lib/theme.ts`: price in ink, `--contract` violet, `--up` /
`--down`, washes; dark mode has its own steps. Pure helpers with vitest: `lib/format.ts`, `lib/url.ts`,
`lib/outlook.ts`.

### Config as data

`backend/config/companies.yaml`: per ticker `name`, `sector`, `news_query` (parenthesised GDELT
query) and `contracts` (first one is shown and scored): `id` (`poly:<slug>`), `venue`, `name`,
`question`, `token_id` (the YES outcome's CLOB token from Gamma `clobTokenIds[0]`, resolved with
`find_markets.py --event-slug`; never a slug), `event_sign` (+1 when YES is good for the stock).
`config.companies()` / `config.company()` are `lru_cache`d like `markets()`: a YAML edit needs a
restart. `.env` is read from the repo root first, then `backend/`; env vars resolve as
`os.getenv(x) or default`.

### Synthetic data is the test substrate

`synthetic.py`: `quiet_market` / `add_jump` (odds series), `equity_sessions` / `add_equity_move(...,
vol_mult=)` (session-only stock bars), `demo_market` / `demo_equity` (the old macro demo, still used
by tests). Tests never touch the network: `test_stocks.py` and `test_api.py` monkeypatch
`equities.backfill_bars` and `polymarket.price_history`, `test_explain.py` stubs `httpx.get` and
`explain._generate`, `test_api.py` also stubs `api.headlines`, `stocks.company_name` and `stocks._now`
and clears every lru plus `_explain_memo` and `stocks._heads_pulled`.

## Current state

Verified live 2026-10-17 (`backend/data/cache.duckdb` is gitignored): NVDA, TSLA, AAPL, MSFT bars for
30 days; six company contracts pulled (NVDA/AAPL largest-company legs 43k 1-minute bars each); NVDA
shows 5 spikes, one with "contract moved 29 min before the stock" (Oct 12), the rest "contract did
not move"; the climate came back with 40 English headlines and four themes; Gemini returns valid JSON
for the stock prompt and prose for the outlook (NVDA read *bearish*, 59%, driven by the contract's
−14 pt 7-day trend). Not yet solid: GDELT per-spike windows often return nothing even at 4 hours,
and its 429 rate makes a cold outlook take minutes (warm first). The open-ended `EQUITY_PARAMS` are
calibrated on one month of four tickers only. Known cosmetic issue: `Spike.headline()` prints a
prob-unit spike with `.0%`, so a sub-1% odds market reads "1% → 0%".
