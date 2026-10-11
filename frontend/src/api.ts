import type {
  Alert,
  AlertsResponse,
  DetectorInputs,
  EquitySeries,
  Explanation,
  Exposure,
  LeadLag,
  Market,
  Series,
  Store,
} from "./types";

export const DEFAULT_NEWS_QUERY = '"Federal Reserve" OR Powell';

/** News search used for a market's "why" when the user hasn't typed an override. */
const NEWS_QUERY_BY_EVENT: Record<string, string> = {
  fed_rates: DEFAULT_NEWS_QUERY,
  inflation: 'CPI OR inflation OR "consumer prices"',
  tariffs: "tariffs OR tariff OR Canada trade",
  recession: 'recession OR "jobs report" OR GDP',
  shutdown: '"government shutdown" OR "spending bill"',
};

export function autoNewsQuery(eventType: string | undefined): string {
  return (eventType && NEWS_QUERY_BY_EVENT[eventType]) || DEFAULT_NEWS_QUERY;
}

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

export function fetchMarkets(): Promise<Market[]> {
  return request<{ markets: Market[] }>("/api/markets").then((r) => r.markets);
}

export function fetchStore(): Promise<Store> {
  return request<Store>("/api/store");
}

export function fetchSeries(marketId: string, signal?: AbortSignal): Promise<Series> {
  return request<Series>(`/api/markets/${encodeURIComponent(marketId)}/series`, { signal });
}

export function fetchAlerts(marketId: string, p: DetectorInputs, signal?: AbortSignal): Promise<AlertsResponse> {
  const q = new URLSearchParams({
    window: String(p.window),
    score_threshold: String(p.score_threshold),
    hold: String(p.hold),
    vol_min_ratio: String(p.vol_min_ratio),
  });
  return request<AlertsResponse>(`/api/markets/${encodeURIComponent(marketId)}/alerts?${q}`, { signal });
}

// Explain and exposure are memoised per key so a card never refetches while the
// replay clock ticks. The backend caches too; this just avoids the round trip.
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

export function fetchExplanation(marketId: string, alert: Alert, query: string): Promise<Explanation> {
  const key = `explain:${marketId}:${alert.kind}:${alert.peak}:${query}`;
  return memoised(key, () =>
    request<Explanation>("/api/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ market_id: marketId, spike: alert, query }),
    }),
  );
}

export function fetchExposure(eventType: string): Promise<Exposure> {
  return memoised(`exposure:${eventType}`, () => request<Exposure>(`/api/exposure/${encodeURIComponent(eventType)}`));
}

export function fetchLeadLag(marketId: string, alert: Alert): Promise<LeadLag> {
  const key = `leadlag:${marketId}:${alert.kind}:${alert.peak}`;
  return memoised(key, () =>
    request<LeadLag>("/api/leadlag", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ market_id: marketId, spike: alert }),
    }),
  );
}

export function fetchEquitySeries(marketId: string, ticker: string, signal?: AbortSignal): Promise<EquitySeries> {
  return request<EquitySeries>(
    `/api/markets/${encodeURIComponent(marketId)}/equities/${encodeURIComponent(ticker)}/series`,
    { signal },
  );
}
