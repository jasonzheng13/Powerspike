import { useEffect, useState } from "react";

import { fetchClimate } from "../api";
import { fmtClock } from "../lib/format";
import type { Climate } from "../types";

/** The last 72 hours of AI headlines, summarised into themes. Shared by every stock. */
export default function ClimatePanel() {
  const [climate, setClimate] = useState<Climate | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    fetchClimate()
      .then((c) => live && setClimate(c))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, []);

  return (
    <section className="panel climate" aria-label="AI news climate">
      <h2>AI news, last 72 hours</h2>
      {error && <p className="error">Climate unavailable: {error}</p>}
      {!climate && !error && <p className="pending">Reading the AI tape…</p>}
      {climate && (
        <>
          <p className="prose">{climate.summary}</p>
          {climate.themes.length > 0 && (
            <ul className="themes">
              {climate.themes.map((t) => (
                <li key={t.theme} className={t.direction}>
                  <span className="theme-name">{t.theme}</span>
                  <span className="theme-dir">{t.direction} for AI stocks</span>
                  <span className="theme-weight" style={{ width: `${Math.round(t.weight * 100)}%` }} />
                </li>
              ))}
            </ul>
          )}
          {climate.headlines.length > 0 ? (
            <ul className="headlines">
              {climate.headlines.slice(0, 8).map((h) => (
                <li key={h.url}>
                  <a href={h.url} target="_blank" rel="noreferrer">
                    {h.title}
                  </a>{" "}
                  <span className="muted">{h.domain}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">No AI headlines came back from the news index just now.</p>
          )}
          <p className="muted small">as of {fmtClock(climate.as_of)} UTC</p>
        </>
      )}
    </section>
  );
}
