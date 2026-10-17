/** Pure formatting helpers, free of React so they can be unit-tested. */

const pad = (n: number) => String(n).padStart(2, "0");
const NY = "America/New_York";

/** "HH:MM" in UTC, or New York wall-clock time with zone "ET". */
export function fmtHM(iso: string, zone: "UTC" | "ET" = "UTC"): string {
  const d = new Date(iso);
  if (zone === "ET") {
    return new Intl.DateTimeFormat("en-US", { timeZone: NY, hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
  }
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/** "YYYY-MM-DD HH:MM" in UTC. */
export function fmtClock(iso: string): string {
  const d = new Date(iso);
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${fmtHM(iso)}`;
}

/** "Dec 10" on the New York calendar. */
export function fmtDay(iso: string): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: NY, month: "short", day: "numeric" }).format(new Date(iso));
}

export function pct(x: number, digits = 0): string {
  return `${(x * 100).toFixed(digits)}%`;
}

export function fmtPrice(x: number, unit: "prob" | "usd"): string {
  if (unit === "prob") return pct(x);
  return `$${x.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** "+9 min" / "−14 min" / "no move" for a lead/lag reaction. Positive = the contract moved after the stock. */
export function fmtLag(lagMin: number | null): string {
  if (lagMin === null) return "no move";
  const r = Math.round(lagMin);
  return r === 0 ? "0 min" : `${r > 0 ? "+" : "−"}${Math.abs(r)} min`;
}

/** Stable identity for a spike across refetches. */
export function spikeKey(s: { kind: string; peak: string }): string {
  return `${s.kind}@${s.peak}`;
}
