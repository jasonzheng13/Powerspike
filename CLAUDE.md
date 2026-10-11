# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Dislocation Desk (Hack Knight 2026, track: *Prediction Markets as a Financial Signal*) watches macro
prediction markets (Fed, CPI, tariffs, shutdown, recession) on Polymarket and Kalshi, flags the moments
the odds reprice, explains the move from news in that window, lists the credit ETFs and companies
exposed, and measures whether the market **led or lagged** those ETFs. Two halves: `backend/` (~1500
lines of Python: ingest, detector, explain, expose, leadlag, FastAPI) and `frontend/` (React 19 +
TypeScript + Vite, Plotly for the charts). `README.md` holds the pitch and the detector write-up.

## Commands

```bash
# backend — run from backend/ (the .venv lives at the repo root)
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env                      # GEMINI_API_KEY, SEC_USER_AGENT, optional GEMINI_MODEL / DD_CACHE_PATH / DD_DATABASE_URL
cd backend
pytest                                    # all tests (synthetic data only, no network)
pytest tests/test_detect.py::test_fat_finger_does_not_fire -q   # one test
uvicorn dislocation_desk.api:app --reload --port 8000           # API; serves frontend/dist at / if built

python scripts/smoke_test_apis.py         # hit each external API once, print shapes
python scripts/find_markets.py fed        # Polymarket search -> YES token_id
python scripts/find_markets.py --kalshi KXFED
python scripts/pull_data.py --start 2025-12-09 --end 2025-12-11   # or --days 7; also pulls the ETFs (--no-equities / --equities-only)
python scripts/run_leadlag.py             # lead/lag of every cached spike vs its ETFs, plus the summary (proof slide)
python scripts/migrate_cache.py           # copy the local DuckDB cache into Tiger Cloud (needs DD_DATABASE_URL)
python scripts/seed_demo_data.py          # synthetic market into the cache (replay/validate only; the API builds demo_market() itself and ignores this row)
python scripts/run_validation.py          # hit rate / false-alarm rate on known_events.yaml

# frontend — run from frontend/
npm install
npm run dev                               # http://localhost:5173, proxies /api -> :8000 (vite.config.ts)
npm run typecheck                         # tsc --noEmit (strict, noUncheckedIndexedAccess)
npm test                                  # vitest: src/lib/replay.test.ts
npm run build                             # tsc + vite build -> frontend/dist
```

There is no linter or formatter configured and no CI.

## Architecture

A five-stage pipeline, each stage a module under `backend/dislocation_desk/`, with the browser owning
only the replay clock:

```
Polymarket ─┐
            ├─► ingest/ ─► DuckDB cache ─► detect.py ─┬─► explain.py (GDELT + Gemini) ─┐
Kalshi ─────┘                 ▲               ▲       ├─► expose.py  (EDGAR + ETF map) ┼─► api.py ─► frontend/src
yfinance ───► ingest/equities ┘               │       └─► leadlag.py (ETF reaction)   ─┘
                                              │
                              replay.py (demo clock; the frontend inlines
                              the same confirmed_at filter in lib/replay.ts)    validate.py (proof slide)
```

Two contracts hold the pipeline together, and most changes should preserve them:

1. **The bar frame.** Every venue client in `ingest/` returns a DataFrame indexed by UTC timestamp with
   columns `price` (0..1 probability) and `volume` — or an *empty* frame (plain RangeIndex) when the API
   returns nothing. `ingest.to_grid()` puts it on a 1-minute grid: price is last-in-bucket then
   forward-filled, volume is summed per bucket. An all-NaN volume column survives the grid as all-NaN,
   and that is how `detect.score_series` recognises a volume-free series and treats it as neutral
   (`vol_conf = 1.0`) rather than failing. No live venue is volume-free any more; the neutral path remains
   for old cached rows and hand-built frames. Kalshi quotes dollar strings and fills a missing candle
   volume with 0, not NaN. Polymarket's `prices-history` has no volume, so `polymarket.history()` merges in
   the Data API `/trades` feed (keyed by the market's `conditionId`, looked up from the token via Gamma):
   price rows carry volume 0 and trade rows carry NaN price, and `to_grid` resolves both (`last()` skips
   the NaN prices). Volume is shares traded on *both* outcomes, the same unit as Kalshi contracts. The
   trades feed is paged backwards by `end` because offsets past ~10k return nothing, and 429s are retried.
   `ingest/cache.py` persists the grid as `bars(market_id, ts, price, volume)` with
   `PRIMARY KEY (market_id, ts)` and an upsert, so re-pulling an overlapping window overwrites
   rather than duplicates; `market_id` is the `id` from `markets.yaml` (there is no venue column).
   **Two storage backends behind one seam:** `cache.connect()` with no argument returns a Tiger Cloud
   (Postgres + TimescaleDB) connection when `DD_DATABASE_URL` is set, else the local DuckDB file; every
   other `cache.*` function dispatches on the connection type. The Tiger side (`ingest/pg.py`) keeps
   `bars` as a hypertable on `ts` plus a real-time **continuous aggregate** `bars_15min` (left-edge
   `time_bucket`, `last(price)`, summed volume) that `cache.load_15min()` reads; on DuckDB the same call
   is a pandas resample. One connection per process (the dev tier has no pooler), reopened if it drops.
   Tests always pass an explicit DuckDB path, so they never see `DD_DATABASE_URL`; `tests/test_cache_pg.py`
   runs only when it is set and writes under the `_test:` prefix. `scripts/migrate_cache.py` copies the
   local file into Tiger. `GET /api/store` reports the backend and per-market freshness for the sidebar.
   Nothing downstream of `to_grid` knows which venue the data came from — though Kalshi stamps candles at
   their period *end* (`end_period_ts`) while Polymarket's `t` is used as-is, so the two venues may sit
   one bar apart. Unlike explain/expose, ingest raises on HTTP errors; `pull_data.py` catches per market
   and prints FAILED instead of exiting nonzero. Over the wire (`/api/markets/{id}/series`) the frame is
   three parallel arrays `ts` (ISO UTC), `price`, `volume`, with `volume: null` for a volume-free series
   and NaN sent as `null` (JSON has no NaN; see `api._num`). **ETFs use the same frame**:
   `ingest/equities.py` turns yfinance 1-minute bars into `price` (dollars) and `volume`, cached as
   `equity:<TICKER>` in the same `bars` table. The one difference is that `session_grid` applies
   `to_grid` *per NYSE session* (09:30–16:00 America/New_York, weekdays, holidays ignored) so a price is
   never forward-filled across the overnight gap; cached ETF frames contain session bars only.
2. **`Spike` with `confirmed_at`.** `detect.detect()` returns `Spike` objects whose `confirmed_at` is the
   earliest time the alert could honestly have been shown live, because the persistence test looks `hold`
   bars *into the future* (`confirmed_at` = peak + `hold` bars). `/api/markets/{id}/alerts` returns *all*
   spikes for the series; `replay.py` and the frontend both filter on `confirmed_at <= now`, which is what
   keeps the demo free of look-ahead. The frontend does not call `replay.py`; `visibleAlerts()` in
   `frontend/src/lib/replay.ts` re-implements the same filter on a client-side clock (`pos` bars revealed,
   advanced by a `setInterval` in `App.tsx`). Anything that adds a forward-looking detector input must push
   `confirmed_at` out accordingly. Two known gaps: drift alerts resample to 15-minute bars labelled by
   the *left* edge, so a drift's `confirmed_at` understates by up to ~14 minutes; and `replay()` runs
   `detect()` once on the full series then reveals by time — that this matches a live truncated run is
   unverified (TODO at `replay.py:30`). **Lead/lag results carry their own `confirmed_at`** (see below)
   because the ETF search looks `lookaround` minutes past the market peak; the card hides the row and
   the ETF chart hides the reaction marker until the clock passes it (`revealed()` in `lib/replay.ts`).

### Detector (`detect.py`)

The thesis is one sentence: *a spike is a move that is big, backed by real money, and sticks.*
`score = |robust z| × vol_conf × persistence`, alert at `score >= 4` with a 60-bar cooldown. Things worth
knowing before editing it:

- Everything works in **log-odds** (`logit`), so 2%→6% counts like 40%→60% should.
- "Big" is a **robust z** — `(Δ − rolling median) / (1.4826 × rolling MAD)` — with the baseline lagged by
  `window` so the move being scored never sits inside its own baseline. Median/MAD rather than
  mean/std so an earlier spike doesn't blind the detector later (`test_robust_z_ignores_earlier_outlier`).
- The series is pre-smoothed with a 5-bar rolling median (`level`), which is why a 1–2 bar fat-finger
  print can't be either end of a "move".
- `detect_drifts` is a separate two-sided **CUSUM** over 15-minute bars, for grinds with no single big
  bar. Its reference mean is **zero on purpose** — a rolling baseline would absorb the very trend it is
  meant to catch. `detect()` drops drift alerts that overlap a jump alert.
- Drifts check volume differently from jumps. A jump needs a *surge* (`vol_min_ratio`× usual for full
  credit); a drift needs only its *usual* volume over its span (ratio ≥ 1 → full credit), because a grind
  trades at a normal pace — `add_drift` in `synthetic.py` keeps volume normal on purpose. Both floor
  "usual" at `min_notional` *dollars at risk* per `window`, converted to shares at the current price
  (`floor_shares`: $100 / min(p, 1−p)), so the ratio stays in shares and is not distorted by the move
  itself, while 500 contracts at 1¢ earn nothing. A drift fires when CUSUM level × credit ≥ `cusum_h`,
  so an under-traded grind fires later rather than never, and a volume-free series keeps credit 1.
- **Tail gate.** A move whose *both* ends lie under `tail` (0.05) or over `1 − tail` scores 0 (jumps)
  or contributes 0 to the CUSUM (drifts): one tick at 1% is ~0.7 logit, and a settled 0–2% contract
  produced 103 alerts in 20 days before this. A move with one end inside the range (3% → 19%) still fires.
- **Dead zones at both ends of a series.** The lagged median/MAD needs `baseline/2` (= 120) bars, so the
  first ~135 bars can never fire — which is why the frontend's "Play from start" rewinds to
  `params.baseline` (240, taken from the alerts response), not 0. Persistence is measured at exactly one
  future bar, `t + hold`, so the last `hold` bars can't fire either.
- Cooldown emits one `Spike` per contiguous over-threshold run and restarts from the *end* of the run.
  Drifts have no cooldown — the CUSUM resets to 0 after each alarm, so a long grind can fire repeatedly.
  The jump/drift overlap test drops a drift whose peak falls in
  `[jump.start, jump.confirmed_at + cooldown minutes]`, which assumes the 1-minute grid.
- All tunables live in `DetectorParams`; four of them are query params on the alerts endpoint and
  sidebar sliders in the frontend (`window`, `score_threshold`, `hold`, `vol_min_ratio`). Tuning belongs
  in that dataclass, not in call sites; the frontend's `DEFAULT_PARAMS` in `App.tsx` mirrors the
  dataclass defaults and should move with them.
- `validate.py` counts a hit when any alert's **`peak`** (not `confirmed_at`) lands within
  `tolerance_min` (60) of the event; drift alerts count too, and uncached events get `fired=None`,
  which `summary()` skips.

### Lead/lag (`leadlag.py`)

Answers "did the market move before or after the ETFs it should hit?" with prices only; news is
never involved. For one `Spike` and one ETF (session bars only):

- **Two kinds of ETF move.** The intraday move is the `window`-bar log return *within the session*
  (a session's first `window` bars have none, so the overnight gap never leaks into them), scored
  with the detector's own `robust_z`. The opening bar instead carries the **gap** (log open minus the
  previous close), scored against the intraday scale stretched by `sqrt(390 / window)` (one session
  of diffusion, a heuristic) with its own lower threshold `gap_sig_z` (2.0; there is one gap per
  session to be wrong about). Intraday needs `sig_z` 3.5: on the cached ETFs a quiet 2-hour window
  clears 2.5 about 24% of the time and 3.5 about 7%.
- **Timing is onset against onset, in trading minutes.** The origin is `spike.start` (start of the
  market move); the ETF reacted at the *first* bar in the search window that clears its threshold;
  `lag_min` is that bar's position minus the origin's, so it counts session bars and a window that
  starts at 15:50 ET continues into the next morning (`spans_close`) rather than being cut at the
  close. Positive = the ETF moved after the market. A reaction inside the first `window` bars after
  an open (other than the gap) is first visible at bar `window`.
- **After hours.** A market onset outside 09:30–16:00 ET is measured from the *next open* and
  flagged `after_hours`; the gap at that open is the first candidate (lag 0). An opening gap that
  precedes an in-session spike counts as the ETF leading (negative lag).
- **`z`/`ret`/`consistent`** come from the first reacting run, so the sign belongs to the move that
  set the timing. The direction check needs no NLP: `expected_move = market direction ×
  markets.yaml event_sign × exposure.yaml expected[etf]` versus `sign(ret)`. `exposure.yaml`
  defines the *event* per type (fed_rates = rate-cut odds, inflation = hotter CPI, …) and
  `event_sign` says how a market's YES price maps to it (`fed-oct-hold` is −1: YES = hold).
- **`status`** separates `reacted`, `quiet` (enough bars, nothing unusual) and `no_data` (fewer than
  `min_bars` scored bars after the origin: not cached, a holiday, the cache ends early). The card
  shows "no data" and the ETF panel is replaced by a caption when the ETF bars do not overlap the
  market window.
- **`confirmed_at`** is the timestamp of the last bar searched (next morning if the window spans the
  close), never earlier than the spike's own; nothing in the result depends on later data.
- **Verdict per spike** (`lead_lag`): needs `min_reactions` (2, or all configured when fewer)
  reacting ETFs, else `no equity move`; all `no_data` → `no data`; an after-hours spike with
  reactions → `market led (overnight)` (the market moved while stocks were closed); otherwise the
  median lag → `market led` / `market lagged` / `concurrent` (±2 trading minutes). `summary()`
  counts the buckets for the proof slide (`scripts/run_leadlag.py`); its median lag is in-session only.
- **Known limits.** One opening gap answers every overnight spike that preceded it, so two markets
  that moved the same night share a reaction (the Oct 7 cache week does exactly this). The gap scale
  is a heuristic. A quiet window still clears 3.5 by chance in one ETF of five or six about a third of
  the time, which is what `min_reactions` guards against. Tunables live in `LeadLagParams`.
  Cross-correlation over lags was rejected: the market series is bursty and forward-filled, so
  1-minute diffs are mostly zero.
- **Synthetic demo.** `demo_equity()` ETFs start moving 6–20 minutes after the demo jump's onset
  (`DEMO_REACTIONS` in `synthetic.py`; XLF stays flat on purpose), which is why `demo_market()`
  starts at 03:00 UTC so the jump lands in the session. The demo drift fires after the close, so its
  lead/lag `confirmed_at` is past the end of the demo series and its card reads "Watching ETFs…"
  for the whole replay; that is honest, not a bug.

### API (`api.py`) and frontend

`api.py` is a thin FastAPI layer; it owns no logic beyond JSON shaping and caching. Routes:
`GET /api/markets` (cached markets from `markets.yaml` plus the synthetic demo, always last),
`GET /api/store` (which backend holds the bars, per-market last bar / bar count / 24 h volume),
`GET /api/markets/{id}/series`, `GET /api/markets/{id}/alerts?window&score_threshold&hold&vol_min_ratio`,
`POST /api/explain {market_id, spike, query}`, `GET /api/exposure/{event_type}` (now includes
`expected`), `GET /api/markets/{id}/equities/{ticker}/series` (market-scoped so `synthetic-demo` serves
`demo_equity()` and real markets read `equity:<TICKER>` from the cache; 404 when uncached) and
`POST /api/leadlag {market_id, spike}`. Series, equity series, lead/lag, explanations
and exposure are `lru_cache`'d per process, so a cache re-pull (`pull_data.py`) needs a server restart to
show up, and EDGAR is hit once per event type instead of once per card per tick. The frontend memoises
explain/exposure again per alert key in `api.ts`, so replay ticks never refetch. `Spike` round-trips
through `spike_to_json`/`spike_from_json`; the explain endpoint takes the alert JSON back verbatim.
If `frontend/dist` exists at import time the API mounts it at `/`, so one process serves the demo.

Frontend state lives in `App.tsx` (market, detector inputs, news query, replay `pos`/`playing`/`speed`,
theme, and the ETF panel's `etf`/`etfPicked`/`equity`/`leadlag`); `?market=<id>` in the URL selects a
market and is kept in sync. Components are function components with hooks: `Sidebar`, `ReplayControls`,
`MarketChart` (imperative `Plotly.react` inside a `useEffect`; colors are read from CSS custom properties
so the chart follows the light/dark tokens in `styles.css`), `EquityChart` (same pattern, under the
market chart on the same x-range; shows the focus alert's market peak and, once revealed, the ETF's
reaction; the ETF defaults to the one that reacted most via `bestTicker` until the user clicks a chip),
`AlertCard` (fetches its own explanation, exposure and lead/lag once; the lead/lag row needs `now`).
`App.tsx` fetches lead/lag for every visible alert (keyed on the set of visible alert keys, so replay
frames do not re-run it) and the "focus alert" is the clicked one, else the latest visible alert whose
lead/lag is already revealed, else the latest visible. Pure helpers and their tests are in
`src/lib/replay.ts`.

### External services and their failure mode

Every outward call degrades instead of raising, because the demo must always render: `explain.explain()`
falls back to a headline template without `GEMINI_API_KEY` or on any exception; `explain.headlines()`
and `expose.edgar_companies()` return `[]` on HTTP or JSON errors (EDGAR also needs `SEC_USER_AGENT`).
Keep that property. The LLM is Google Gemini (free tier) via the `google-genai` SDK:
`genai.Client(api_key=...).models.generate_content(model, contents=prompt)`; a blocked or empty response
(`resp.text` is `None`) also falls back. The model defaults to `gemini-flash-lite-latest` (override with
`GEMINI_MODEL`); free-tier quotas are a few requests per minute, which the per-alert caching keeps well under.
GDELT rate-limits to one request per 5 seconds and answers a plain-text scolding (not JSON) when
exceeded, which `headlines()` turns into `[]` — so bursts of alert cards can all show "Cause unclear".

The GDELT news query is **not** per-market config — it is a single sidebar text box defaulting to
`'"Federal Reserve" OR Powell'` (`DEFAULT_NEWS_QUERY` in both `api.py` and `api.ts`), so non-Fed
markets get Fed headlines unless the user edits it.

### Config as data

`backend/config/markets.yaml` lists the watched markets; each carries an `event_type` key that indexes
`backend/config/exposure.yaml` (ETFs, EDGAR query, `direction_note` shown on the card and fed to the
explainer). Adding a market or an event type is a YAML edit, not a code change.
`backend/dislocation_desk/config.py` is the only place that reads `.env` or those files. `.env` is
read from the repo root first, then `backend/`. Both YAML loaders are `@lru_cache`'d, so a YAML edit
needs a process restart to show up. Env vars resolve as `os.getenv(x) or default`, so the empty values
in a copied `.env.example` fall through to the defaults (`DD_CACHE_PATH` → `backend/data/cache.duckdb`,
`GEMINI_MODEL` → `gemini-flash-lite-latest`, `DD_DATABASE_URL` → empty, meaning DuckDB). Polymarket entries need `token_id` — the YES outcome's **CLOB
token** (Gamma `clobTokenIds[0]`), not the market id or slug; Kalshi entries need both `series_ticker`
and `ticker` because the candlesticks URL uses both.

### Synthetic data is the test substrate

`synthetic.py` builds markets with a real jump, a fat-finger print and a slow drift injected at known
bars; `backend/tests/` asserts the detector fires on the first and third and not the second. Most tests
build their own single-event series (jump at bar 700 with volume ×6, fat-finger at bar 500, drift at
bar 600); only `test_demo_market_end_to_end` and `tests/test_api.py` use `demo_market()` (fat-finger at
420, jump at 700, drift from 1000). Tests never touch the network: `test_api.py` points the cache at a
temp DuckDB and stubs `headlines` and `edgar_companies`. `demo_market()` is also what the API serves as
`synthetic-demo` before any real data is cached — built in-process, not read from the cache — and real
cached markets are listed first so they take the default slot once they exist.

## Current state

Working and tested: `detect.py`, `replay.py`, `ingest/cache.py`, `ingest/equities.py`, `leadlag.py`,
`api.py`, the frontend on synthetic and cached data. Kalshi ingest is live-tested (dollar-string prices, `volume_fp`). Five markets and 17 ETFs are
cached locally as of 2026-10-10 (`backend/data/cache.duckdb` is gitignored; re-pull with `pull_data.py`;
yfinance only serves 1-minute bars for the last ~30 days, so the ETF side of an older demo window
cannot be re-pulled). On that week all three real spikes fired outside NYSE hours, so their lead/lag is
measured from the next open: two read `market led (overnight)`, one has too few reacting ETFs.

Validated on real events as of 2026-10-10: `validation/known_events.yaml` scores the settled Kalshi
contracts `kalshi-fed-sep` (KXFED-26SEP-T3.75) and `kalshi-cpi-aug` on the Aug jobs / PPI / CPI releases
(all 08:30 ET) plus three quiet controls: 6/6, hit rate 1.0, false-alarm rate 0.0 (`run_validation.py`;
data via `pull_data.py --start 2026-08-28 --end 2026-09-16 --no-equities`; Polymarket rejects a window
that long at 1-min fidelity, Kalshi does not). Kalshi closes a contract 5 min before the release it settles
on, so validate on the releases *before* close, and the candle after close reports bid 0 / ask 1, which
`kalshi.parse_candles` now drops instead of writing 50%. On the cached live week the only surviving alert
is the Oct 7 CPI block (40k contracts); the two Kalshi Fed alerts it used to show were one-lot trades
bouncing across a 5¢ spread. The Kalshi price is the last trade when the candle has one, else the
midpoint, and ~90% of its 1-min price changes are midpoint moves with no trade.

Not yet live-verified: `explain.headlines` returned no articles in a manual probe (GDELT rate limit or
query shape, unclear), `expose.edgar_companies` has returned `[]` so far. Polymarket volume
(`trades_volume`) is live-tested as of 2026-10-09. Trading is bursty: on every live market the *median*
15-minute window has zero volume, so `DetectorParams.min_notional` floors the "usual" level; without it
any single trade earned full `vol_conf`. `min_scale` (0.05 logit) is the binding scale on 95%+ of bars
on the live markets, so there the "robust z" is effectively a fixed 0.2-logit threshold; on the liquid
settled Fed contract it binds far less. On a thin market like `tariffs` (391 shares in a week) neither
jumps nor drifts fire. Three of the five live markets (`fed-oct-hold`, `recession`, `tariffs`) changed
price 28, 7 and ~1900 times in the week with almost no trades, so nothing can fire on them.
Known cosmetic issue: `Spike.headline()` formats
prices with `.0%`, so a sub-1% market reads "1% → 0%".
