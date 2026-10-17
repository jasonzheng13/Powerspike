import { useEffect, useState } from "react";

import { fetchStockExplanation } from "../api";
import { fmtDay, fmtHM, pct, spikeKey } from "../lib/format";
import { fmtContractLag, themeTone } from "../lib/outlook";
import type { Days, Spike, StockExplanation } from "../types";

interface Props {
  ticker: string;
  days: Days;
  spike: Spike;
  selected: boolean;
  onSelect: () => void;
  /** Called once the explanation has resolved (or failed) so the outlook can start. */
  onExplained: (key: string) => void;
}

/** One spike: when, how big, why, and whether the contract moved first. Fetches its own explanation. */
export default function SpikeCard({ ticker, days, spike, selected, onSelect, onExplained }: Props) {
  const [why, setWhy] = useState<StockExplanation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = spikeKey(spike);

  useEffect(() => {
    let live = true;
    setWhy(null);
    setError(null);
    fetchStockExplanation(ticker, days, spike)
      .then((r) => live && setWhy(r))
      .catch((e: Error) => live && setError(e.message))
      .finally(() => live && onExplained(key));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ticker, days, key]);

  const ll = spike.contract_leadlag;
  const llTone = ll?.verdict === "contract led" ? "led" : ll?.verdict === "stock led" ? "lagged" : "quiet";
  const overnight = spike.headline.includes("across the open");

  return (
    <article className={`spike ${spike.direction}${selected ? " selected" : ""}`} aria-current={selected || undefined}>
      <button type="button" className="spike-head" onClick={onSelect}>
        <span className="spike-when">
          {fmtDay(spike.peak)}, {overnight ? "at the open" : `${fmtHM(spike.start, "ET")} ET`}
        </span>
        <span className="spike-headline">{spike.headline}</span>
      </button>
      <div className="spike-body">
        <p className="spike-why">
          {why ? (
            why.why
          ) : error ? (
            <span className="error">Explanation failed: {error}</span>
          ) : (
            <span className="pending">Reading the news around this move…</span>
          )}
        </p>
        <div className="chips">
          {why && (
            <span className={`chip theme ${themeTone(why.theme)}`} title="What the news was about">
              {why.theme}
            </span>
          )}
          {why?.ai_related && <span className="chip ai">AI news</span>}
          <span
            className={`chip contract ${llTone}`}
            title={ll ? `${ll.verdict}${ll.consistent === false ? ", against the expected direction" : ""}` : undefined}
          >
            {fmtContractLag(ll)}
          </span>
        </div>
        <p className="spike-meta">
          {spike.z.toFixed(1)}σ, {spike.volume_ratio !== null ? `${spike.volume_ratio.toFixed(1)}× usual volume, ` : ""}
          {pct(spike.persistence)} of the move still held 30 minutes later
        </p>
        {why && why.headlines.length > 0 && (
          <details open={selected}>
            <summary>
              {why.headlines.length} headline{why.headlines.length === 1 ? "" : "s"} in the window
            </summary>
            <ul>
              {why.headlines.map((h) => (
                <li key={h.url}>
                  <a href={h.url} target="_blank" rel="noreferrer">
                    {h.title}
                  </a>{" "}
                  <span className="muted">{h.domain}</span>
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </article>
  );
}
