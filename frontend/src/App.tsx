import { useEffect, useMemo, useState } from "react";

import { autoNewsQuery, fetchAlerts, fetchEquitySeries, fetchExposure, fetchLeadLag, fetchMarkets, fetchSeries, fetchStore } from "./api";
import AlertCard from "./components/AlertCard";
import EquityChart from "./components/EquityChart";
import MarketChart from "./components/MarketChart";
import ReplayControls from "./components/ReplayControls";
import Sidebar from "./components/Sidebar";
import { alertKey, bestTicker, clamp, fmtClock, revealed, visibleAlerts } from "./lib/replay";
import { useMarketSummaries } from "./lib/summaries";
import { applyTheme, initialTheme, type Theme } from "./lib/theme";
import type { Alert, AlertsResponse, DetectorInputs, EquitySeries, LeadLag, Market, Series, Store } from "./types";

const DEFAULT_PARAMS: DetectorInputs = {
  window: 15,
  score_threshold: 4,
  hold: 30,
  vol_min_ratio: 2,
};
/** At 1x a full replay lasts this long, whatever the market's length; Nx plays N times faster. */
const REPLAY_MS_AT_1X = 60_000;
const SPEEDS = [1, 2, 5, 10];

/** A lead/lag result that compares timing, as opposed to "no equity move" / "no data". */
function hasVerdict(ll: LeadLag): boolean {
  return ll.verdict !== "no equity move" && ll.verdict !== "no data";
}

/** Do any ETF bars fall inside the market series' window? */
function overlaps(equity: EquitySeries, series: Series): boolean {
  const lo = Date.parse(series.ts[0]!);
  const hi = Date.parse(series.ts[series.ts.length - 1]!);
  return equity.ts.some((t) => {
    const ms = Date.parse(t);
    return ms >= lo && ms <= hi;
  });
}

/** `?market=<id>` makes a demo day linkable; falls back to the first cached market. */
function marketFromUrl(): string {
  return new URLSearchParams(window.location.search).get("market") ?? "";
}

function syncUrl(marketId: string): void {
  const url = new URL(window.location.href);
  url.searchParams.set("market", marketId);
  window.history.replaceState(null, "", url);
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [marketId, setMarketId] = useState<string>(marketFromUrl);
  const [params, setParams] = useState<DetectorInputs>(DEFAULT_PARAMS);
  const [newsOverride, setNewsOverride] = useState("");
  const [speed, setSpeed] = useState(1);

  const summaries = useMarketSummaries(markets, params);
  const [store, setStore] = useState<Store | null>(null);

  const [series, setSeries] = useState<Series | null>(null);
  const [alertsResp, setAlertsResp] = useState<AlertsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [pos, setPos] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [picked, setPicked] = useState<{
    key: string;
    at: { x: number; y: number };
  } | null>(null);

  // Lead/lag panel: the event type's ETFs, the one on screen, its bars, and each visible alert's result.
  const [etfs, setEtfs] = useState<string[]>([]);
  const [etf, setEtf] = useState<string>("");
  const [etfPicked, setEtfPicked] = useState(false);
  const [equity, setEquity] = useState<EquitySeries | null>(null);
  const [equityNote, setEquityNote] = useState<string | null>(null);
  const [leadlags, setLeadLags] = useState<Record<string, LeadLag>>({});

  useEffect(() => applyTheme(theme), [theme]);

  // Markets once; real cached markets come first, the synthetic demo last.
  useEffect(() => {
    fetchMarkets()
      .then((ms) => {
        setMarkets(ms);
        setMarketId((cur) =>
          ms.some((m) => m.id === cur) ? cur : (ms[0]?.id ?? ""),
        );
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // Where the bars live (Tiger Cloud or the local DuckDB file); shown in the sidebar status line.
  useEffect(() => {
    fetchStore().then(setStore).catch(() => setStore(null));
  }, []);

  // Series on market change; the clock rewinds to the end (everything visible).
  useEffect(() => {
    if (!marketId) return;
    syncUrl(marketId);
    const ctl = new AbortController();
    setSeries(null);
    setAlertsResp(null);
    setPlaying(false);
    setPicked(null);
    fetchSeries(marketId, ctl.signal)
      .then((s) => {
        setSeries(s);
        setPos(s.ts.length);
        setError(null);
      })
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => ctl.abort();
  }, [marketId]);

  // Alerts whenever the detector sliders move (debounced).
  useEffect(() => {
    if (!marketId) return;
    const ctl = new AbortController();
    const t = setTimeout(() => {
      fetchAlerts(marketId, params, ctl.signal)
        .then(setAlertsResp)
        .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    }, 200);
    return () => {
      clearTimeout(t);
      ctl.abort();
    };
  }, [marketId, params]);

  // Replay clock.
  const n = series?.ts.length ?? 0;
  const baseline = alertsResp?.params.baseline ?? 240;
  const minPos = Math.min(baseline, n);
  useEffect(() => {
    if (!playing || n === 0) return;
    let raf = 0;
    let last = performance.now();
    const frame = (t: number) => {
      const dt = t - last;
      last = t;
      setPos((p) =>
        Math.min(n, p + ((n - minPos) / REPLAY_MS_AT_1X) * speed * dt),
      );
      raf = requestAnimationFrame(frame);
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, n, minPos]);
  useEffect(() => {
    if (playing && pos >= n) setPlaying(false);
  }, [playing, pos, n]);

  const market = markets.find((m) => m.id === marketId);
  const autoQuery = autoNewsQuery(market?.event_type);
  const newsQuery = newsOverride.trim() || autoQuery;
  const startReplay = () => {
    setPos(minPos);
    setPlaying(true);
  };
  const now = series?.ts[Math.floor(clamp(pos, 1, n)) - 1];
  const alerts = useMemo(
    () => visibleAlerts(alertsResp?.alerts ?? [], now),
    [alertsResp, now],
  );

  const selected = picked
    ? alerts.find((a) => alertKey(a) === picked.key)
    : undefined;

  // The alert whose timing the ETF panel shows: the clicked one; else the latest visible alert with a
  // revealed comparison verdict; else the latest with any revealed result; else the latest visible
  // (its row reads "Watching ETFs…").
  const revealedFor = (a: Alert): LeadLag | undefined => {
    const ll = leadlags[alertKey(a)];
    return ll !== undefined && revealed(ll.confirmed_at, now) ? ll : undefined;
  };
  const newestFirst = [...alerts].reverse();
  const focusAlert =
    selected ??
    newestFirst.find((a) => {
      const ll = revealedFor(a);
      return ll !== undefined && hasVerdict(ll);
    }) ??
    newestFirst.find((a) => revealedFor(a) !== undefined) ??
    alerts[alerts.length - 1];
  const leadlag = focusAlert ? (leadlags[alertKey(focusAlert)] ?? null) : null;

  // ETF list for the market's event type (memoised in api.ts); reset the pick on market change.
  useEffect(() => {
    if (!market) return;
    let live = true;
    setEtfPicked(false);
    fetchExposure(market.event_type)
      .then((x) => {
        if (!live) return;
        setEtfs(x.etfs);
        setEtf((cur) => (x.etfs.includes(cur) ? cur : (x.etfs[0] ?? "")));
      })
      .catch((e: Error) => live && setEquityNote(e.message));
    return () => {
      live = false;
    };
  }, [market]);

  // Lead/lag for every visible alert (a handful per market; the API caches per spike and api.ts
  // memoises per alert). Keyed on the set of visible alert keys, not the array, so replay frames
  // do not re-run it; guarded on the alerts belonging to the current market.
  useEffect(() => setLeadLags({}), [marketId]);
  const visibleKeys = useMemo(() => alerts.map(alertKey).join("|"), [alerts]);
  useEffect(() => {
    if (!market || alertsResp?.market_id !== market.id) return;
    const pending = alerts.filter((a) => !leadlags[alertKey(a)]);
    if (pending.length === 0) return;
    let live = true;
    for (const a of pending) {
      const key = alertKey(a);
      fetchLeadLag(market.id, a)
        .then((r) => live && setLeadLags((cur) => (cur[key] ? cur : { ...cur, [key]: r })))
        .catch(() => undefined);
    }
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market, alertsResp, visibleKeys, leadlags]);

  // Unless the user picked an ETF, follow the one that reacted most once a verdict is revealed. A
  // lone reaction under "no equity move" is not followed: it is the false alarm the verdict guards against.
  const leadlagRevealed = leadlag !== null && revealed(leadlag.confirmed_at, now);
  useEffect(() => {
    if (etfPicked || !leadlagRevealed || !leadlag || !hasVerdict(leadlag)) return;
    setEtf((cur) => bestTicker(leadlag.reactions, cur));
  }, [etfPicked, leadlagRevealed, leadlag]);

  // The ETF's bars.
  useEffect(() => {
    if (!marketId || !etf) return;
    const ctl = new AbortController();
    setEquity(null);
    setEquityNote(null);
    fetchEquitySeries(marketId, etf, ctl.signal)
      .then((s) => setEquity(s))
      .catch((e: Error) => e.name !== "AbortError" && setEquityNote(e.message));
    return () => ctl.abort();
  }, [marketId, etf]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setPicked(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="layout">
      <Sidebar
        markets={markets}
        marketId={marketId}
        onMarket={setMarketId}
        params={params}
        onParams={setParams}
        newsQuery={newsOverride}
        autoQuery={autoQuery}
        onNewsQuery={setNewsOverride}
        summaries={summaries}
        store={store}
      />
      <main className="main">
        <div className="header">
          <span className="header-market">{market?.name ?? ""}</span>
          <div className="header-actions">
            <div className="replay-bar">
              <button
                className="btn primary"
                onClick={startReplay}
                disabled={playing}
              >
                ▶ Play
              </button>
              <button
                className="btn"
                onClick={() => setPlaying(false)}
                disabled={!playing}
              >
                ⏹ Stop
              </button>
              <div
                className="speed-pills"
                role="group"
                aria-label="Replay speed"
              >
                {SPEEDS.map((x) => (
                  <button
                    key={x}
                    className={`pill${speed === x ? " on" : ""}`}
                    onClick={() => setSpeed(x)}
                  >
                    {x}×
                  </button>
                ))}
              </div>
            </div>
            <button
              className="btn ghost"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? "☀ Light" : "☾ Dark"}
            </button>
          </div>
        </div>

        {error && <div className="panel status error">{error}</div>}
        {!series && !error && (
          <div className="panel status">Loading market…</div>
        )}

        {series && market && (
          <>
            <div className="chart-wrap">
              <MarketChart
                series={series}
                pos={pos}
                alerts={alerts}
                theme={theme}
                onAlertClick={(a, at) => setPicked({ key: alertKey(a), at })}
              />
              {selected && picked && (
                <div
                  className="popover"
                  style={{
                    left: `clamp(236px, ${picked.at.x}px, calc(100% - 236px))`,
                    top: picked.at.y,
                  }}
                >
                  <AlertCard
                    alert={selected}
                    market={market}
                    newsQuery={newsQuery}
                    now={now}
                    onClose={() => setPicked(null)}
                  />
                </div>
              )}
            </div>
            <ReplayControls
              pos={Math.floor(pos)}
              min={minPos}
              max={n}
              playing={playing}
              onPlay={startReplay}
              onStop={() => setPlaying(false)}
              onSeek={(p) => {
                setPlaying(false);
                setPos(p);
              }}
            />
            <div className="caption">
              Replay clock: {now ? fmtClock(now) : "—"} UTC · {alerts.length}{" "}
              alert(s) so far
              {alertsResp === null && " · running detector…"}
            </div>
            {equity && series.ts.length > 0 && overlaps(equity, series) ? (
              <EquityChart
                series={equity}
                now={now}
                range={[series.ts[0]!, series.ts[series.ts.length - 1]!]}
                tickers={etfs}
                ticker={etf}
                onTicker={(t) => {
                  setEtfPicked(true);
                  setEtf(t);
                }}
                focus={focusAlert ? { alert: focusAlert, leadlag, revealed: leadlagRevealed } : undefined}
                theme={theme}
              />
            ) : (
              <div className="caption">
                {equityNote
                  ? `ETF panel: ${equityNote}`
                  : equity
                    ? `ETF panel: no ${etf} bars cached for this window (run scripts/pull_data.py)`
                    : etf
                      ? `Loading ${etf}…`
                      : "ETF panel: no exposure configured for this market"}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
