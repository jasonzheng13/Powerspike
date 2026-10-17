/** Copy and tones for the outlook card and the lead/lag chips. */

import type { ContractLeadLag, OutlookLabel, Theme_ } from "../types";

export type Tone = "up" | "down" | "flat";

export function labelTone(label: OutlookLabel): Tone {
  return label === "bullish" ? "up" : label === "bearish" ? "down" : "flat";
}

/** One fragment per verdict, in the viewer's words. */
export function fmtContractLag(ll: ContractLeadLag | null): string {
  if (ll === null) return "no contract for this company";
  const mins = Math.abs(Math.round(ll.lag_min ?? 0));
  switch (ll.verdict) {
    case "contract led":
      return `contract moved ${mins} min before the stock`;
    case "stock led":
      return `stock moved first, contract followed ${mins} min later`;
    case "concurrent":
      return "contract and stock moved together";
    case "contract quiet":
      return "contract did not move";
    default:
      return "no contract data for this window";
  }
}

const AI_THEMES: ReadonlySet<Theme_> = new Set(["AI model release", "chip export rules", "capex"]);

export function themeTone(theme: Theme_): "ai" | "plain" {
  return AI_THEMES.has(theme) ? "ai" : "plain";
}
