import { useEffect, useState } from "react";

import { fetchExplanation, fetchExposure, fetchLeadLag } from "../api";
import { fmtHM, fmtLag, pct, revealed } from "../lib/replay";
import type { Alert, EtfReaction, Explanation, Exposure, LeadLag, Market } from "../types";

interface Props {
  alert: Alert;
  market: Market;
  newsQuery: string;
  /** Replay clock; the lead/lag row stays hidden until its own confirmed_at. */
  now: string | undefined;
  /** When set, the card is shown as a popover on the chart: adds a close button and opens the news. */
  onClose?: () => void;
}

function ReactionChip({ r }: { r: EtfReaction }) {
  const cls = r.status !== "reacted" ? "quiet" : r.consistent === true ? "ok" : r.consistent === false ? "bad" : "";
  const mark = r.consistent === true ? " ✓" : r.consistent === false ? " ✗" : "";
  const label = r.status === "no_data" ? "no data" : fmtLag(r.lag_min);
  const title =
    r.status === "no_data"
      ? `${r.ticker}: too few ETF bars after the move (not cached, holiday, or the cache ends here)`
      : r.status === "quiet"
        ? `${r.ticker}: no unusual move within the window`
        : `${r.ticker}: ${r.ret !== null ? (r.ret * 100).toFixed(2) + "%" : ""} (${r.z?.toFixed(1)}σ` +
          `${r.after_hours && r.lag_min === 0 ? ", opening gap" : ""})` +
          (r.spans_close ? ", window ran into the next session" : "") +
          (r.consistent === false ? ", against the expected direction" : "");
  return (
    <span className={`chip ${cls}`} title={title}>
      {r.ticker} {label}
      {mark}
    </span>
  );
}

export default function AlertCard({ alert, market, newsQuery, now, onClose }: Props) {
  const [why, setWhy] = useState<Explanation | null>(null);
  const [exposure, setExposure] = useState<Exposure | null>(null);
  const [leadlag, setLeadLag] = useState<LeadLag | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setWhy(null);
    fetchExplanation(market.id, alert, newsQuery)
      .then((r) => live && setWhy(r))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [market.id, alert, newsQuery]);

  useEffect(() => {
    let live = true;
    fetchExposure(market.event_type)
      .then((r) => live && setExposure(r))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [market.event_type]);

  useEffect(() => {
    let live = true;
    setLeadLag(null);
    fetchLeadLag(market.id, alert)
      .then((r) => live && setLeadLag(r))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [market.id, alert]);

  const afterHours = leadlag?.reactions.some((r) => r.after_hours) ?? false;
  const names = exposure
    ? [...exposure.etfs, ...exposure.companies.slice(0, 5).map((c) => c.company)]
    : [];

  return (
    <article className="panel card">
      {onClose && (
        <button className="close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      )}
      <div className="title">
        <span className={`tag ${alert.kind}`}>{alert.kind.toUpperCase()}</span>
        <strong>{market.name}</strong> — {alert.headline}
      </div>
      <div>
        <strong>Why:</strong>{" "}
        {why ? why.why : <span className="pending">Explaining the move…</span>}
      </div>
      <div>
        <strong>Exposed{exposure ? ` (${exposure.label})` : ""}:</strong>{" "}
        {exposure ? (
          <span className="chips">
            {names.map((n) => (
              <span className="chip" key={n}>
                {n}
              </span>
            ))}
          </span>
        ) : (
          <span className="pending">Looking up exposure…</span>
        )}
      </div>
      {exposure?.note && <div className="meta">{exposure.note}</div>}
      <div>
        <strong>Lead/lag:</strong>{" "}
        {!leadlag ? (
          <span className="pending">Comparing with ETFs…</span>
        ) : !revealed(leadlag.confirmed_at, now) ? (
          <span className="pending">Watching ETFs… confirms {fmtHM(leadlag.confirmed_at)} UTC</span>
        ) : (
          <>
            <strong>{leadlag.verdict}</strong>
            {leadlag.median_lag !== null && leadlag.verdict !== "market led (overnight)" && ` (median ${fmtLag(leadlag.median_lag)})`}
            {afterHours && " · market moved while stocks were closed; measured from the next open"}{" "}
            <span className="chips">
              {leadlag.reactions.map((r) => (
                <ReactionChip r={r} key={r.ticker} />
              ))}
            </span>
          </>
        )}
      </div>
      <div className="meta">
        Move {fmtHM(alert.start)}–{fmtHM(alert.peak)}, confirmed {fmtHM(alert.confirmed_at)} UTC · score{" "}
        {alert.score.toFixed(1)} · held {pct(alert.persistence)}
      </div>
      {why && why.headlines.length > 0 && (
        <details open={Boolean(onClose)}>
          <summary>{why.headlines.length} headlines in window</summary>
          <ul>
            {why.headlines.map((h) => (
              <li key={h.url}>
                <a href={h.url} target="_blank" rel="noreferrer">
                  {h.title}
                </a>{" "}
                · {h.domain}
              </li>
            ))}
          </ul>
        </details>
      )}
      {error && <div className="meta error">{error}</div>}
    </article>
  );
}
