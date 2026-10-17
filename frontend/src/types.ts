/** Wire types for the backend API (backend/powerspike/api.py). */

export type Days = 7 | 30;

export interface ContractRef {
  id: string;
  name: string;
  question: string;
  /** +1 when a rising YES price is good for the stock, -1 when it is bad. */
  event_sign: number;
}

export interface Company {
  ticker: string;
  name: string;
  sector: string | null;
  news_query: string;
  contracts: ContractRef[];
}

/** The company's contract odds, 5-minute points, for the chart. */
export interface ContractSeries extends ContractRef {
  ts: string[];
  price: number[];
}

/** GET /api/stocks/{ticker}: 1-minute session bars plus the configured contract. */
export interface StockSeries {
  ticker: string;
  name: string;
  configured: boolean;
  sector: string | null;
  /** Newest bar (ISO UTC); also the freshness token behind every cache. */
  as_of: string;
  ts: string[];
  /** Dollars. */
  price: number[];
  volume: (number | null)[];
  contract: ContractSeries | null;
}

/** detect.DetectorParams as the backend resolved it (EQUITY_PARAMS for stocks). */
export interface DetectorParams {
  window: number;
  baseline: number;
  min_scale: number;
  vol_min_ratio: number;
  min_notional: number;
  tail: number;
  hold: number;
  score_threshold: number;
  cooldown: number;
  smooth: number;
  onset_frac: number;
  gap_stretch: number;
  space: "logit" | "log";
}

export type SpikeKind = "jump" | "drift";

export type ContractVerdict = "contract led" | "stock led" | "concurrent" | "contract quiet" | "no data";

/** Mirrors leadlag.ContractReaction: how the company's contract behaved around a stock spike. */
export interface ContractLeadLag {
  contract_id: string;
  status: "reacted" | "quiet" | "no_data";
  reaction_at: string | null;
  /** Wall-clock minutes after the stock's onset; negative = the contract moved first. */
  lag_min: number | null;
  z: number | null;
  ret: number | null;
  expected_move: number;
  consistent: boolean | null;
  verdict: ContractVerdict;
  confirmed_at: string;
}

/** Mirrors detect.Spike plus `direction`, `headline` and the contract's reaction. */
export interface Spike {
  market_id: string;
  kind: SpikeKind;
  start: string;
  peak: string;
  confirmed_at: string;
  p_before: number;
  p_after: number;
  z: number;
  volume_ratio: number | null;
  persistence: number;
  score: number;
  unit: "prob" | "usd";
  direction: "up" | "down";
  headline: string;
  /** null when the company has no configured contract. */
  contract_leadlag: ContractLeadLag | null;
}

export interface SpikesResponse {
  ticker: string;
  as_of: string;
  params: DetectorParams;
  spikes: Spike[];
}

export interface Headline {
  title: string;
  url: string;
  domain: string;
  seendate: string;
}

export type Theme_ =
  | "AI model release"
  | "capex"
  | "chip export rules"
  | "earnings"
  | "macro"
  | "regulation"
  | "product"
  | "other";

export interface StockExplanation {
  why: string;
  theme: Theme_;
  ai_related: boolean;
  stock_sentiment: "positive" | "negative" | "mixed";
  headlines: Headline[];
}

export interface ClimateTheme {
  theme: Theme_;
  direction: "bullish" | "bearish" | "mixed";
  weight: number;
}

export interface Climate {
  summary: string;
  themes: ClimateTheme[];
  headlines: Headline[];
  as_of: string;
}

export type OutlookLabel = "bullish" | "bearish" | "neutral";

export interface Driver {
  kind: "theme" | "contract" | "coverage";
  text: string;
  weight: number;
}

export interface Outlook {
  ticker: string;
  name: string;
  label: OutlookLabel;
  confidence: number;
  score: number;
  n_spikes: number;
  drivers: Driver[];
  prose: string;
  climate_summary: string;
  contract: { id: string; name: string; trend_7d_pts: number; move_24h_pts: number; led_share: number | null } | null;
  disclaimer: string;
  as_of: string;
}
