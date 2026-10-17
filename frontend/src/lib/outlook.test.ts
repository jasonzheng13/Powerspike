import { describe, expect, it } from "vitest";

import type { ContractLeadLag } from "../types";
import { fmtContractLag, labelTone, themeTone } from "./outlook";

const ll = (verdict: ContractLeadLag["verdict"], lag_min: number | null): ContractLeadLag => ({
  contract_id: "poly:t",
  status: verdict === "no data" ? "no_data" : verdict === "contract quiet" ? "quiet" : "reacted",
  reaction_at: null,
  lag_min,
  z: null,
  ret: null,
  expected_move: 1,
  consistent: null,
  verdict,
  confirmed_at: "2025-12-10T16:00:00+00:00",
});

describe("labelTone", () => {
  it("maps each outlook label to a tone", () => {
    expect(labelTone("bullish")).toBe("up");
    expect(labelTone("bearish")).toBe("down");
    expect(labelTone("neutral")).toBe("flat");
  });
});

describe("fmtContractLag", () => {
  it("reads as a sentence fragment for every verdict", () => {
    expect(fmtContractLag(ll("contract led", -23))).toBe("contract moved 23 min before the stock");
    expect(fmtContractLag(ll("stock led", 10.4))).toBe("stock moved first, contract followed 10 min later");
    expect(fmtContractLag(ll("concurrent", 1))).toBe("contract and stock moved together");
    expect(fmtContractLag(ll("contract quiet", null))).toBe("contract did not move");
    expect(fmtContractLag(ll("no data", null))).toBe("no contract data for this window");
    expect(fmtContractLag(null)).toBe("no contract for this company");
  });
});

describe("themeTone", () => {
  it("flags AI themes", () => {
    expect(themeTone("AI model release")).toBe("ai");
    expect(themeTone("chip export rules")).toBe("ai");
    expect(themeTone("capex")).toBe("ai");
    expect(themeTone("earnings")).toBe("plain");
    expect(themeTone("other")).toBe("plain");
  });
});
