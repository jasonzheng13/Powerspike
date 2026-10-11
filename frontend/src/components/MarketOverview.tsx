import { pct } from "../lib/replay";
import type { Summary } from "../lib/summaries";
import type { Market } from "../types";

interface Props {
  markets: Market[];
  marketId: string;
  data: Record<string, Summary>;
  onMarket: (id: string) => void;
}

const W = 120;
const H = 32;
const MAX_POINTS = 80;

/** Evenly thinned polyline points; the sparkline only needs the shape. */
function sparkPoints(price: number[]): string {
  const step = Math.max(1, Math.ceil(price.length / MAX_POINTS));
  const pts = price.filter((_, i) => i % step === 0 || i === price.length - 1);
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  const span = hi - lo || 1;
  return pts
    .map((p, i) => `${((i / Math.max(1, pts.length - 1)) * W).toFixed(1)},${(H - 2 - ((p - lo) / span) * (H - 4)).toFixed(1)}`)
    .join(" ");
}

/** One tile per market: sparkline, latest odds, and a badge when the detector fired. */
export default function MarketOverview({ markets, marketId, data, onMarket }: Props) {
  if (markets.length === 0) return null;

  return (
    <div className="overview" role="list" aria-label="All markets">
      {markets.map((m) => {
        const s = data[m.id];
        const last = s?.series.price[s.series.price.length - 1];
        const first = s?.series.price[0];
        const delta = last !== undefined && first !== undefined ? last - first : 0;
        const fired = (s?.alerts.length ?? 0) > 0;
        return (
          <button
            key={m.id}
            role="listitem"
            className={`tile${m.id === marketId ? " active" : ""}${fired ? " fired" : ""}`}
            onClick={() => onMarket(m.id)}
          >
            <span className="tile-name" title={m.name}>
              {m.name}
            </span>
            {s ? (
              <>
                <svg viewBox={`0 0 ${W} ${H}`} className="spark" preserveAspectRatio="none" aria-hidden="true">
                  <polyline points={sparkPoints(s.series.price)} fill="none" stroke="currentColor" strokeWidth="1.5" />
                </svg>
                <span className="tile-meta">
                  <b>{pct(last ?? 0)}</b>
                  <span className={delta >= 0 ? "up" : "down"}>
                    {delta >= 0 ? "▲" : "▼"} {Math.abs(delta * 100).toFixed(1)}
                  </span>
                  {fired && <span className="badge">{s.alerts.length} alert{s.alerts.length > 1 ? "s" : ""}</span>}
                </span>
              </>
            ) : (
              <span className="tile-meta">loading…</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
