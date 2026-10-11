import Plotly from "plotly.js-basic-dist-min";
import { useEffect, useRef } from "react";

import { chartColors, type Theme } from "../lib/theme";
import type { Alert, Series } from "../types";

interface Props {
  series: Series;
  /** Number of bars revealed by the replay clock. */
  pos: number;
  alerts: Alert[];
  theme: Theme;
  /** Fired when a spike marker is clicked; x/y are pixels relative to the chart panel. */
  onAlertClick?: (alert: Alert, at: { x: number; y: number }) => void;
}

const PRICE_DOMAIN: [number, number] = [0.3, 1];
const VOLUME_DOMAIN: [number, number] = [0, 0.24];

/** Probability line over volume bars, with each alert's move shaded and its peak marked. */
export default function MarketChart({ series, pos, alerts, theme, onAlertClick }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const onClick = useRef(onAlertClick);
  onClick.current = onAlertClick;

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const c = chartColors();
    const hasVolume = series.volume !== null;
    const k = Math.floor(pos);
    const ts = series.ts.slice(0, k);
    // Between bars, extend the line to a point interpolated toward the next bar so it glides.
    const frac = pos - k;
    const head = frac > 0 && k >= 1 && k < series.ts.length;
    const lineX: string[] = head
      ? [
          ...ts,
          new Date(
            Date.parse(series.ts[k - 1]!) + frac * (Date.parse(series.ts[k]!) - Date.parse(series.ts[k - 1]!)),
          ).toISOString(),
        ]
      : ts;
    const lineY = series.price.slice(0, k);
    if (head) lineY.push(series.price[k - 1]! + frac * (series.price[k]! - series.price[k - 1]!));
    const priceDomain = hasVolume ? PRICE_DOMAIN : ([0, 1] as [number, number]);

    const data: Plotly.Data[] = [
      {
        type: "scatter",
        mode: "lines",
        name: "Probability",
        x: lineX,
        y: lineY,
        line: { color: c.series, width: 2 },
        hovertemplate: "%{y:.2~%}<extra>Probability</extra>",
      },
    ];
    if (hasVolume) {
      data.push({
        type: "bar",
        name: "Volume",
        x: ts,
        y: series.volume!.slice(0, k),
        yaxis: "y2",
        marker: { color: c.volume, opacity: 0.6, line: { width: 0 } },
        hovertemplate: "%{y:,.0f}<extra>Volume</extra>",
      });
    }
    if (alerts.length) {
      data.push({
        type: "scatter",
        mode: "markers",
        name: "Alert",
        x: alerts.map((a) => a.peak),
        y: alerts.map((a) => a.p_after),
        text: alerts.map((a) => `${a.kind.toUpperCase()} · ${a.headline} (click for news)`),
        hovertemplate: "%{text}<extra></extra>",
        marker: {
          symbol: "x",
          size: 14,
          color: alerts.map((a) => (a.kind === "jump" ? c.jump : c.drift)),
          line: { width: 2, color: c.surface },
        },
      });
    }

    const shapes: Partial<Plotly.Shape>[] = alerts.map((a) => ({
      type: "rect",
      xref: "x",
      yref: "paper",
      x0: a.start,
      x1: a.peak,
      y0: priceDomain[0],
      y1: priceDomain[1],
      fillcolor: a.kind === "jump" ? c.jumpWash : c.driftWash,
      line: { width: 0 },
      layer: "below",
    }));

    const axis = {
      gridcolor: c.grid,
      zerolinecolor: c.grid,
      linecolor: c.axis,
      tickfont: { color: c.muted, size: 11 },
    };
    const title = (text: string) => ({ text, font: { color: c.muted, size: 11 } });
    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: c.surface,
      plot_bgcolor: c.surface,
      font: { family: "system-ui, -apple-system, 'Segoe UI', sans-serif", color: c.ink },
      margin: { l: 52, r: 16, t: 12, b: 36 },
      showlegend: false,
      hovermode: "x unified",
      hoverlabel: { bgcolor: c.surface, bordercolor: c.axis, font: { color: c.ink } },
      xaxis: { ...axis, range: [series.ts[0], series.ts[series.ts.length - 1]], showspikes: false },
      yaxis: { ...axis, domain: priceDomain, tickformat: ".2~%", title: title("Probability"), fixedrange: true },
      ...(hasVolume
        ? { yaxis2: { ...axis, domain: VOLUME_DOMAIN, title: title("Volume"), fixedrange: true, nticks: 3 } }
        : {}),
      shapes,
    };

    void Plotly.react(node, data, layout, { displayModeBar: false, responsive: true }).then(() => {
      // Only the spike markers are clickable; the Alert trace is always the last one.
      const gd = node as unknown as {
        on: (ev: string, fn: (e: unknown) => void) => void;
        removeAllListeners: (ev: string) => void;
      };
      gd.removeAllListeners("plotly_click");
      gd.on("plotly_click", (e) => {
        const ev = e as { points: { curveNumber: number; pointIndex: number }[]; event: MouseEvent };
        const pt = ev.points.find((p) => p.curveNumber === data.length - 1 && alerts.length > 0);
        const alert = pt && alerts[pt.pointIndex];
        if (!alert) return;
        const box = node.getBoundingClientRect();
        onClick.current?.(alert, { x: ev.event.clientX - box.left, y: ev.event.clientY - box.top });
      });
    });
  }, [series, pos, alerts, theme]);

  useEffect(() => {
    const node = el.current;
    return () => {
      if (node) Plotly.purge(node);
    };
  }, []);

  return (
    <div className="panel chart">
      <div ref={el} />
    </div>
  );
}
