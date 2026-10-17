import type { Climate, Company, Days, Outlook, Spike, SpikesResponse, StockExplanation, StockSeries } from "./types";
import { spikeKey } from "./lib/format";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, init);
  if (!r.ok) {
    let detail = r.statusText;
    try {
      detail = ((await r.json()) as { detail?: string }).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${r.status} ${detail}`);
  }
  return (await r.json()) as T;
}

// Per-key promise memo so a card never refetches while the page re-renders; the backend caches too.
const memo = new Map<string, Promise<unknown>>();

function memoised<T>(key: string, load: () => Promise<T>): Promise<T> {
  let p = memo.get(key) as Promise<T> | undefined;
  if (!p) {
    p = load().catch((e: unknown) => {
      memo.delete(key); // let a failed call be retried
      throw e;
    });
    memo.set(key, p);
  }
  return p;
}

const t = (ticker: string) => encodeURIComponent(ticker.toUpperCase());

export function fetchCompanies(): Promise<Company[]> {
  return memoised("companies", () => request<{ companies: Company[] }>("/api/companies").then((r) => r.companies));
}

export function fetchStock(ticker: string, days: Days, signal?: AbortSignal): Promise<StockSeries> {
  return request<StockSeries>(`/api/stocks/${t(ticker)}?days=${days}`, { signal });
}

export function fetchSpikes(ticker: string, days: Days, signal?: AbortSignal): Promise<SpikesResponse> {
  return request<SpikesResponse>(`/api/stocks/${t(ticker)}/spikes?days=${days}`, { signal });
}

/** The server explains its own copy of the spike; the body only identifies it. */
export function fetchStockExplanation(ticker: string, days: Days, spike: Spike): Promise<StockExplanation> {
  return memoised(`explain:${ticker}:${spikeKey(spike)}`, () =>
    request<StockExplanation>(`/api/stocks/${t(ticker)}/explain?days=${days}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spike: { kind: spike.kind, peak: spike.peak } }),
    }),
  );
}

export function fetchClimate(): Promise<Climate> {
  return memoised("climate", () => request<Climate>("/api/climate"));
}

export function fetchOutlook(ticker: string, days: Days, signal?: AbortSignal): Promise<Outlook> {
  return request<Outlook>(`/api/stocks/${t(ticker)}/outlook?days=${days}`, { signal });
}
