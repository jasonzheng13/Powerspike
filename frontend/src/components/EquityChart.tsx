import Plotly from "plotly.js-basic-dist-min";
import { useEffect, useRef } from "react";

import { chartColors, type Theme } from "../lib/theme";
import { fmtLag } from "../lib/replay";
import type { Alert, EquitySeries, EtfReaction, LeadLag } from "../types";

interface Props {
  series: EquitySeries;
  /** Replay clock: bars at or before this instant are drawn. */
  now: string | undefined;
  /** x-range shared with the market chart so the two panels line up. */
  range: [string, string];
  tickers: string[];
  ticker: string;
  onTicker: (t: string) => void;
  /** The alert in focus and its lead/lag, once revealed; both peaks get marked. */
  focus?: { alert: Alert; leadlag: LeadLag | null; revealed: boolean };
  theme: Theme;
}

/** Break the line where bars are more than 5 minutes apart (overnight, weekends). */
function withGaps(ts: string[], price: number[]): { x: string[]; y: (number | null)[] } {
  const x: string[] = [];
  const y: (number | null)[] = [];
  for (let i = 0; i < ts.length; i++) {
    const t = ts[i]!;
    if (i > 0 && Date.parse(t) - Date.parse(ts[i - 1]!) > 5 * 60_000) {
      x.push(t);
      y.push(null);
    }
    x.push(t);
    y.push(price[i]!);
  }
  return { x, y };
}

/** One ETF under the market chart: price during session hours, the market peak and the ETF's reaction marked. */
export default function EquityChart({ series, now, range, tickers, ticker, onTicker, focus, theme }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const reaction: EtfReaction | undefined =
    focus?.revealed ? focus.leadlag?.reactions.find((r) => r.ticker === series.ticker) : undefined;

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const c = chartColors();
    const nowMs = now ? Date.parse(now) : -Infinity;
    let k = 0;
    while (k < series.ts.length && Date.parse(series.ts[k]!) <= nowMs) k++;
    const { x, y } = withGaps(series.ts.slice(0, k), series.price.slice(0, k));

    const data: Plotly.Data[] = [
      {
        type: "scatter",
        mode: "lines",
        name: series.ticker,
        x,
        y,
        connectgaps: false,
        line: { color: c.ink, width: 1.5 },
        hovertemplate: `%{y:.2f}<extra>${series.ticker}</extra>`,
      },
    ];
    const shapes: Partial<Plotly.Shape>[] = [];
    const annotations: Partial<Plotly.Annotations>[] = [];
    if (focus) {
      const col = focus.alert.kind === "jump" ? c.jump : c.drift;
      shapes.push({ type: "line", xref: "x", yref: "paper", x0: focus.alert.start, x1: focus.alert.start, y0: 0, y1: 1,
        line: { color: col, width: 1.5, dash: "dot" } });
      annotations.push({ x: focus.alert.start, y: 1, xref: "x", yref: "paper", text: "market moves", showarrow: false,
        yanchor: "bottom", font: { color: col, size: 11 } });
    }
    if (focus && reaction?.status === "reacted" && reaction.etf_peak && reaction.lag_min !== null) {
      const i = series.ts.indexOf(reaction.etf_peak);
      const py = i >= 0 ? series.price[i] : undefined;
      // Lag is in trading minutes, so the wash runs from the market onset (or the open) to the reaction bar.
      const origin = reaction.after_hours || reaction.spans_close
        ? series.ts[Math.max(0, i - Math.round(reaction.lag_min))] ?? focus.alert.start
        : focus.alert.start;
      shapes.push({ type: "rect", xref: "x", yref: "paper", x0: origin, x1: reaction.etf_peak, y0: 0, y1: 1,
        fillcolor: c.jumpWash, line: { width: 0 }, layer: "below" });
      if (py !== undefined) {
        data.push({
          type: "scatter",
          mode: "markers",
          name: "ETF reaction",
          x: [reaction.etf_peak],
          y: [py],
          marker: { symbol: "diamond", size: 12, color: c.series, line: { width: 2, color: c.surface } },
          hovertemplate: `${series.ticker} reacted ${fmtLag(reaction.lag_min)}<extra></extra>`,
        });
      }
    }

    const axis = { gridcolor: c.grid, zerolinecolor: c.grid, linecolor: c.axis, tickfont: { color: c.muted, size: 11 } };
    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: c.surface,
      plot_bgcolor: c.surface,
      font: { family: "system-ui, -apple-system, 'Segoe UI', sans-serif", color: c.ink },
      margin: { l: 52, r: 16, t: 18, b: 36 },
      showlegend: false,
      hovermode: "x unified",
      hoverlabel: { bgcolor: c.surface, bordercolor: c.axis, font: { color: c.ink } },
      xaxis: { ...axis, range, showspikes: false },
      yaxis: { ...axis, title: { text: `${series.ticker} ($)`, font: { color: c.muted, size: 11 } }, fixedrange: true },
      shapes,
      annotations,
    };
    void Plotly.react(node, data, layout, { displayModeBar: false, responsive: true });
  }, [series, now, range, focus, reaction, theme]);

  useEffect(() => {
    const node = el.current;
    return () => {
      if (node) Plotly.purge(node);
    };
  }, []);

  const headline = !focus
    ? "Pick an alert to compare timing"
    : !focus.revealed
      ? "Watching ETFs…"
      : reaction
        ? reaction.status === "no_data"
          ? `${series.ticker}: no bars after the move`
          : reaction.status === "quiet"
            ? `${series.ticker}: no unusual move`
            : focus.leadlag?.verdict === "no equity move"
              ? `${series.ticker} alone crossed the threshold (${fmtLag(reaction.lag_min)}); too few ETFs for a verdict`
              : reaction.after_hours && reaction.lag_min === 0
                ? `${series.ticker} gapped at the next open`
                : `${series.ticker} moved ${fmtLag(reaction.lag_min)} ${reaction.after_hours ? "after the next open" : "vs the market onset"}`
        : "";

  return (
    <div className="panel chart equity">
      <div className="equity-head">
        <span className="verdict">{focus?.revealed && focus.leadlag ? focus.leadlag.verdict : "Exposed ETF"}</span>
        <span className="caption">{headline}</span>
        <span className="chips">
          {tickers.map((t) => (
            <button type="button" className={`chip pick${t === ticker ? " active" : ""}`} key={t} onClick={() => onTicker(t)}>
              {t}
            </button>
          ))}
        </span>
      </div>
      <div ref={el} />
    </div>
  );
}
