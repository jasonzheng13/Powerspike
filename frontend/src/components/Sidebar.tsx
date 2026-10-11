import { fmtClock } from "../lib/replay";
import type { Summary } from "../lib/summaries";
import type { DetectorInputs, Market, Store } from "../types";

const VENUE_LABEL: Record<Market["venue"], string> = {
  polymarket: "Polymarket",
  kalshi: "Kalshi",
  synthetic: "Demo",
};

interface Props {
  markets: Market[];
  marketId: string;
  onMarket: (id: string) => void;
  params: DetectorInputs;
  onParams: (p: DetectorInputs) => void;
  /** User override; empty means the market's automatic query is used. */
  newsQuery: string;
  autoQuery: string;
  onNewsQuery: (q: string) => void;
  summaries: Record<string, Summary>;
  store: Store | null;
}

interface SliderProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  format?: (v: number) => string;
  onChange: (v: number) => void;
}

function Slider({
  label,
  value,
  min,
  max,
  step = 1,
  format = String,
  onChange,
}: SliderProps) {
  return (
    <label className="field">
      <span>
        {label} <b>{format(value)}</b>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    </label>
  );
}

function fmtBars(s: Store): string {
  const n = s.markets.reduce((a, m) => a + m.bars, 0);
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

function storeTitle(s: Store): string {
  return s.markets.map((m) => `${m.market_id}: ${m.bars} bars, last ${fmtClock(m.last_bar)} UTC`).join("\n");
}

export default function Sidebar(p: Props) {
  const set = (patch: Partial<DetectorInputs>) =>
    p.onParams({ ...p.params, ...patch });
  const all = Object.values(p.summaries);
  const alertCount = all.reduce((n, x) => n + x.alerts.length, 0);
  const biggest = Math.max(0, ...all.flatMap((x) => x.alerts.map((a) => Math.abs(a.p_after - a.p_before))));
  const hasReal = p.markets.some((m) => m.venue !== "synthetic");
  const lastTs = all
    .filter((x) => x.series.ts.length > 0)
    .map((x) => x.series.ts[x.series.ts.length - 1]!)
    .sort()
    .pop();

  return (
    <aside className="sidebar">
      <div className="brand">
        <h1>Dislocation Desk</h1>
        <p>Which event odds just broke, why, and which names are exposed.</p>
      </div>

      <div className="status-line">
        <span className={`dot${hasReal ? " live" : ""}`} />
        {hasReal ? `Market data through ${lastTs ? fmtClock(lastTs) : "…"} UTC` : "Demo data (synthetic)"}
      </div>
      {p.store && (
        <div className="status-line store" title={storeTitle(p.store)}>
          <span className="venue">{p.store.backend === "tiger" ? "Tiger Cloud" : "DuckDB"}</span>
          {p.store.backend === "tiger"
            ? `hypertable · 15-min continuous aggregate · ${fmtBars(p.store)} bars`
            : `local file · ${fmtBars(p.store)} bars`}
        </div>
      )}

      <div className="stats">
        <div className="stat">
          <b>{p.markets.length}</b>
          <span>Markets watched</span>
        </div>
        <div className="stat">
          <b>{alertCount}</b>
          <span>Alerts found</span>
        </div>
        <div className="stat">
          <b>{biggest > 0 ? `${(biggest * 100).toFixed(0)} pts` : "—"}</b>
          <span>Biggest move</span>
        </div>
      </div>

      <h2>▦ Markets</h2>
      <div className="market-list" role="listbox" aria-label="Markets">
        {p.markets.map((m) => {
          const n = p.summaries[m.id]?.alerts.length ?? 0;
          return (
            <button
              key={m.id}
              role="option"
              aria-selected={m.id === p.marketId}
              className={`market-item${m.id === p.marketId ? " active" : ""}`}
              onClick={() => p.onMarket(m.id)}
            >
              <span className="market-name">{m.name}</span>
              <span className="market-sub">
                <span className="venue">{VENUE_LABEL[m.venue]}</span>
                {n > 0 && <span className="alert-dot">● {n} alert{n > 1 ? "s" : ""}</span>}
              </span>
            </button>
          );
        })}
      </div>

      <h2>◈ Legend</h2>
      <ul className="legend">
        <li>
          <span className="swatch x jump">✕</span>
          <span>
            <b>Jump</b> — a sudden move, backed by real trading, that held. Click it for the why.
          </span>
        </li>
        <li>
          <span className="swatch x drift">✕</span>
          <span>
            <b>Drift</b> — a slow grind in one direction.
          </span>
        </li>
        <li>
          <span className="swatch band" />
          <span>
            <b>Shaded area</b> — the stretch of time the move happened.
          </span>
        </li>
      </ul>

      <details className="advanced">
        <summary>⚙ Advanced</summary>
        <p className="help">Tune how picky the spike alarm is. The defaults work well.</p>
        <h3>Detector</h3>
        <Slider
          label="Jump window (min)"
          value={p.params.window}
          min={5}
          max={60}
          onChange={(v) => set({ window: v })}
        />
        <Slider
          label="Alert threshold (score)"
          value={p.params.score_threshold}
          min={2}
          max={10}
          step={0.5}
          format={(v) => v.toFixed(1)}
          onChange={(v) => set({ score_threshold: v })}
        />
        <Slider
          label="Must hold for (min)"
          value={p.params.hold}
          min={5}
          max={120}
          onChange={(v) => set({ hold: v })}
        />
        <Slider
          label="Volume needed (× usual)"
          value={p.params.vol_min_ratio}
          min={1.5}
          max={10}
          step={0.5}
          format={(v) => `${v.toFixed(1)}×`}
          onChange={(v) => set({ vol_min_ratio: v })}
        />

        <h3>News search</h3>
        <label className="field">
          <span>Override query (blank = automatic)</span>
          <input
            type="text"
            value={p.newsQuery}
            placeholder={p.autoQuery}
            onChange={(e) => p.onNewsQuery(e.target.value)}
          />
        </label>
      </details>
      <footer className="side-footer">Hack Knight 2026 · Prediction Markets as a Financial Signal</footer>
    </aside>
  );
}
