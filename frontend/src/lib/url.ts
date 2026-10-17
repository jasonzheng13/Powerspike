/** `?t=NVDA&days=30` keeps a search linkable. */

import type { Days } from "../types";

const SYMBOL = /^[A-Z][A-Z0-9.\-]{0,9}$/;

export function parseQuery(search: string): { ticker: string; days: Days } {
  const q = new URLSearchParams(search);
  const raw = (q.get("t") ?? "").trim().toUpperCase();
  const ticker = SYMBOL.test(raw) ? raw : "";
  const days: Days = q.get("days") === "7" ? 7 : 30;
  return { ticker, days };
}

export function queryString(ticker: string, days: Days): string {
  const q = new URLSearchParams();
  if (ticker) q.set("t", ticker);
  if (days !== 30) q.set("days", String(days));
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function syncUrl(ticker: string, days: Days): void {
  const url = new URL(window.location.href);
  url.search = queryString(ticker, days);
  window.history.replaceState(null, "", url);
}
