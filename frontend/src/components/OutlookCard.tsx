import { pct } from "../lib/format";
import { labelTone } from "../lib/outlook";
import type { Outlook } from "../types";

interface Props {
  outlook: Outlook | null;
  loading: boolean;
  error: string | null;
  /** Spikes still being explained; the outlook waits for them. */
  pending: number;
}

/** The verdict: one loud word, then exactly what it rests on. */
export default function OutlookCard({ outlook, loading, error, pending }: Props) {
  if (error) {
    return (
      <section className="panel outlook" aria-live="polite">
        <p className="error">Outlook unavailable: {error}</p>
      </section>
    );
  }
  if (!outlook) {
    return (
      <section className="panel outlook" aria-live="polite">
        <div className="verdict flat skeleton">scoring</div>
        <p className="muted">
          {pending > 0
            ? `Reading the news around ${pending} spike${pending === 1 ? "" : "s"}. The news index allows one request every 15 seconds, so this can take a minute or two.`
            : loading
              ? "Weighing the AI news climate against this stock's history."
              : "Waiting for spikes."}
        </p>
      </section>
    );
  }
  const tone = labelTone(outlook.label);
  return (
    <section className="panel outlook" aria-live="polite">
      <div className={`verdict ${tone}`}>{outlook.label}</div>
      <div className="confidence" title={`confidence ${pct(outlook.confidence)}`}>
        <span className={`confidence-bar ${tone}`} style={{ width: `${Math.round(outlook.confidence * 100)}%` }} />
        <span className="confidence-label">
          {pct(outlook.confidence)} confidence from {outlook.n_spikes} spike{outlook.n_spikes === 1 ? "" : "s"}
        </span>
      </div>
      <p className="prose">{outlook.prose}</p>
      {outlook.drivers.length > 0 && (
        <ul className="drivers">
          {outlook.drivers.map((d, i) => (
            <li key={i} className={d.kind}>
              {d.text}
            </li>
          ))}
        </ul>
      )}
      <p className="disclaimer">{outlook.disclaimer}</p>
    </section>
  );
}
