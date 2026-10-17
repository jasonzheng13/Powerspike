import Plotly from "plotly.js-basic-dist-min";
import { useEffect, useMemo, useRef } from "react";

import { fmtPrice } from "../lib/format";
import { chartColors, type Theme } from "../lib/theme";
import type { Spike, StockSeries } from "../types";

interface Props {
  stock: StockSeries;
  spikes: Spike[];
  selectedKey: string | null;
  theme: Theme;
  onSpikeClick?: (spike: Spike) => void;
}

const NY = "America/New_York";
const nyFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: NY,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** ISO UTC -> "YYYY-MM-DD HH:MM" on the New York clock, so the axis can cut nights and weekends out. */
export function toNY(iso: string): string {
  const parts = Object.fromEntries(nyFmt.formatToParts(new Date(iso)).map((p) => [p.type, p.value]));
  const hour = parts.hour === "24" ? "00" : parts.hour;
  return `${parts.year}-${parts.month}-${parts.day} ${hour}:${parts.minute}`;
}

/** Break the line where bars are more than 5 minutes apart (overnight, weekends). */
function withGaps(ts: string[], price: number[]): { x: string[]; y: (number | null)[] } {
  const x: string[] = [];
  const y: (number | null)[] = [];
  for (let i = 0; i < ts.length; i++) {
    const t = ts[i]!;
    if (i > 0 && Date.parse(t) - Date.parse(ts[i - 1]!) > 5 * 60_000) {
      x.push(toNY(t));
      y.push(null);
    }
    x.push(toNY(t));
    y.push(price[i]!);
  }
  return { x, y };
}

type Domain = [number, number];

/**
 * Three rows on one time axis: price with every spike marked, volume, and the company's contract odds.
 * Each row keeps its own y scale (never a dual axis). Nights and weekends are cut out of the axis.
 */
export default function StockChart({ stock, spikes, selectedKey, theme, onSpikeClick }: Props) {
  const el = useRef<HTMLDivElement>(null);
  const onClick = useRef(onSpikeClick);
  onClick.current = onSpikeClick;

  const priceLine = useMemo(() => withGaps(stock.ts, stock.price), [stock]);
  const volumeX = useMemo(() => stock.ts.map(toNY), [stock]);
  const contractX = useMemo(() => stock.contract?.ts.map(toNY) ?? [], [stock]);

  useEffect(() => {
    const node = el.current;
    if (!node) return;
    const c = chartColors();
    const contract = stock.contract && stock.contract.ts.length > 0 ? stock.contract : null;
    const rows: { price: Domain; volume: Domain; contract: Domain | null } = contract
      ? { price: [0.5, 1], volume: [0.34, 0.45], contract: [0, 0.26] }
      : { price: [0.3, 1], volume: [0, 0.22], contract: null };

    const data: Plotly.Data[] = [
      {
        type: "scatter",
        mode: "lines",
        name: stock.ticker,
        x: priceLine.x,
        y: priceLine.y,
        connectgaps: false,
        line: { color: c.price, width: 1.6 },
        hovertemplate: `$%{y:,.2f}<extra>${stock.ticker}</extra>`,
      },
      {
        type: "bar",
        name: "Volume",
        x: volumeX,
        y: stock.volume,
        yaxis: "y2",
        marker: { color: c.volume, line: { width: 0 } },
        hovertemplate: "%{y:,.0f} shares<extra>Volume</extra>",
      },
    ];
    if (contract) {
      data.push({
        type: "scatter",
        mode: "lines",
        name: "Contract",
        x: contractX,
        y: contract.price,
        yaxis: "y3",
        connectgaps: true,
        line: { color: c.contract, width: 1.6, shape: "hv" },
        hovertemplate: `%{y:.0%}<extra>${contract.name}</extra>`,
      });
    }
    if (spikes.length) {
      data.push({
        type: "scatter",
        mode: "markers",
        name: "Spike",
        x: spikes.map((s) => toNY(s.peak)),
        y: spikes.map((s) => s.p_after),
        text: spikes.map((s) => `${s.headline} (click for the story)`),
        hovertemplate: "%{text}<extra></extra>",
        marker: {
          symbol: spikes.map((s) => (s.direction === "up" ? "triangle-up" : "triangle-down")),
          size: spikes.map((s) => (`${s.kind}@${s.peak}` === selectedKey ? 18 : 13)),
          color: spikes.map((s) => (s.direction === "up" ? c.up : c.down)),
          line: { width: 2, color: c.surface },
        },
      });
    }

    const shapes: Partial<Plotly.Shape>[] = spikes.map((s) => ({
      type: "rect",
      xref: "x",
      yref: "paper",
      x0: toNY(s.start),
      x1: toNY(s.peak),
      y0: rows.price[0],
      y1: rows.price[1],
      fillcolor: s.direction === "up" ? c.upWash : c.downWash,
      line: { width: 0 },
      layer: "below",
    }));

    const axis = { gridcolor: c.grid, zerolinecolor: c.grid, linecolor: c.axis, tickfont: { color: c.muted, size: 11 } };
    const title = (text: string) => ({ text, font: { color: c.muted, size: 11 } });
    const layout: Partial<Plotly.Layout> = {
      paper_bgcolor: c.surface,
      plot_bgcolor: c.surface,
      font: { family: "system-ui, -apple-system, 'Segoe UI', sans-serif", color: c.ink },
      margin: { l: 56, r: 12, t: 10, b: 34 },
      showlegend: false,
      hovermode: "x unified",
      hoverlabel: { bgcolor: c.surface, bordercolor: c.axis, font: { color: c.ink } },
      bargap: 0,
      xaxis: {
        ...axis,
        showspikes: false,
        rangebreaks: [{ bounds: ["sat", "mon"] }, { bounds: [16, 9.5], pattern: "hour" }],
        tickformat: "%b %d",
      },
      yaxis: { ...axis, domain: rows.price, tickprefix: "$", title: title(`${stock.ticker} price`), fixedrange: true },
      yaxis2: { ...axis, domain: rows.volume, title: title("Volume"), fixedrange: true, nticks: 3, showgrid: false },
      ...(rows.contract
        ? {
            yaxis3: {
              ...axis,
              domain: rows.contract,
              tickformat: ".0%",
              title: title("Contract odds"),
              fixedrange: true,
              nticks: 4,
            },
          }
        : {}),
      shapes,
    };

    void Plotly.react(node, data, layout, { displayModeBar: false, responsive: true }).then(() => {
      const gd = node as unknown as {
        on: (ev: string, fn: (e: unknown) => void) => void;
        removeAllListeners: (ev: string) => void;
      };
      gd.removeAllListeners("plotly_click");
      gd.on("plotly_click", (e) => {
        const ev = e as { points: { curveNumber: number; pointIndex: number }[] };
        const pt = ev.points.find((p) => p.curveNumber === data.length - 1 && spikes.length > 0);
        const spike = pt && spikes[pt.pointIndex];
        if (spike) onClick.current?.(spike);
      });
    });
  }, [stock, spikes, selectedKey, theme, priceLine, volumeX, contractX]);

  useEffect(() => {
    const node = el.current;
    return () => {
      if (node) Plotly.purge(node);
    };
  }, []);

  const last = stock.price[stock.price.length - 1];
  return (
    <section className="panel chart" aria-label={`${stock.ticker} price chart`}>
      <div className="chart-head">
        <span className="chart-name">
          {stock.name} <span className="muted">{stock.ticker}</span>
        </span>
        {last !== undefined && <span className="chart-last">{fmtPrice(last, "usd")}</span>}
        {stock.contract && <span className="chart-contract">Contract: {stock.contract.question}</span>}
      </div>
      <div ref={el} className="chart-body" />
    </section>
  );
}
