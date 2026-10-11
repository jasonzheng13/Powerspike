/** Wire types for the backend API (backend/dislocation_desk/api.py). */

export interface Market {
  id: string;
  name: string;
  venue: "polymarket" | "kalshi" | "synthetic";
  event_type: string;
}

/** GET /api/store: which backend holds the bars and per-market freshness. */
export interface Store {
  backend: "tiger" | "duckdb";
  markets: { market_id: string; last_bar: string; bars: number; volume_24h: number | null }[];
}

export interface Series {
  market_id: string;
  /** ISO-8601 UTC timestamps on a 1-minute grid. */
  ts: string[];
  /** Probability in [0, 1]. */
  price: number[];
  /** Per-bar volume; null when the venue reports none (Polymarket). */
  volume: (number | null)[] | null;
}

/** The four detector tunables exposed in the sidebar. */
export interface DetectorInputs {
  window: number;
  score_threshold: number;
  hold: number;
  vol_min_ratio: number;
}

/** Full DetectorParams dataclass as the backend resolved it. */
export interface DetectorParams extends DetectorInputs {
  baseline: number;
  min_scale: number;
  cooldown: number;
  smooth: number;
  drift_bar: number;
  drift_baseline: number;
  cusum_k: number;
  cusum_h: number;
  eps: number;
}

export type AlertKind = "jump" | "drift";

/** Mirrors detect.Spike plus `direction` and `headline`. */
export interface Alert {
  market_id: string;
  kind: AlertKind;
  start: string;
  peak: string;
  /** Earliest time the alert could honestly be shown live; the replay filters on this. */
  confirmed_at: string;
  p_before: number;
  p_after: number;
  z: number;
  volume_ratio: number | null;
  persistence: number;
  score: number;
  direction: "up" | "down";
  headline: string;
}

export interface AlertsResponse {
  market_id: string;
  params: DetectorParams;
  alerts: Alert[];
}

export interface Headline {
  title: string;
  url: string;
  domain: string;
  seendate: string;
}

export interface Explanation {
  why: string;
  headlines: Headline[];
}

export interface Exposure {
  label: string;
  etfs: string[];
  /** Sign of each ETF's move when the event's odds rise (+1 / -1 / 0); from exposure.yaml. */
  expected: Record<string, number>;
  companies: { company: string; filings: number }[];
  note: string;
}

/** One ETF's bars for a market's lead/lag panel (session hours only, dollars not probabilities). */
export interface EquitySeries {
  market_id: string;
  ticker: string;
  ts: string[];
  price: number[];
  volume: (number | null)[];
}

/** Mirrors leadlag.EtfReaction. */
export interface EtfReaction {
  ticker: string;
  /** "no_data" = too few ETF bars after the market onset to say anything. */
  status: "reacted" | "quiet" | "no_data";
  /** First bar where the ETF's move became detectable; null when it never did. */
  etf_peak: string | null;
  /** Trading minutes after the market onset (or the next open when after_hours); negative = ETF moved first. */
  lag_min: number | null;
  z: number | null;
  /** Log return of the reacting move: the intraday window, or the opening gap when lag is 0 after hours. */
  ret: number | null;
  expected_move: number;
  consistent: boolean | null;
  after_hours: boolean;
  /** The search window continued into the next session (spike shortly before the close). */
  spans_close: boolean;
  confirmed_at: string;
}

export type LeadLagVerdict =
  | "market led"
  | "market lagged"
  | "concurrent"
  | "market led (overnight)"
  | "no equity move"
  | "no data";

/** Mirrors leadlag.LeadLag. Carries its own confirmed_at because the search looks past the peak. */
export interface LeadLag {
  market_id: string;
  kind: AlertKind;
  peak: string;
  reactions: EtfReaction[];
  verdict: LeadLagVerdict;
  median_lag: number | null;
  confirmed_at: string;
}
