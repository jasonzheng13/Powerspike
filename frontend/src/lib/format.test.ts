import { describe, expect, it } from "vitest";

import { fmtClock, fmtDay, fmtHM, fmtLag, fmtPrice, pct, spikeKey } from "./format";

describe("time formatting", () => {
  it("prints UTC regardless of host zone", () => {
    expect(fmtHM("2025-12-10T23:40:00+00:00")).toBe("23:40");
    expect(fmtClock("2025-12-10T23:40:00+00:00")).toBe("2025-12-10 23:40");
  });
  it("prints New York wall-clock time for the stock views", () => {
    expect(fmtHM("2025-12-10T14:30:00+00:00", "ET")).toBe("09:30");
    expect(fmtDay("2025-12-10T14:30:00+00:00")).toBe("Dec 10");
    expect(fmtDay("2025-07-01T13:30:00+00:00")).toBe("Jul 1");
  });
});

describe("numbers", () => {
  it("percent", () => {
    expect(pct(0.614)).toBe("61%");
    expect(pct(0.614, 1)).toBe("61.4%");
  });
  it("prices by unit", () => {
    expect(fmtPrice(182.1, "usd")).toBe("$182.10");
    expect(fmtPrice(1234.5, "usd")).toBe("$1,234.50");
    expect(fmtPrice(0.614, "prob")).toBe("61%");
  });
});

describe("fmtLag", () => {
  it("formats a lag in minutes with its sign, and 'no move' for null", () => {
    expect(fmtLag(9)).toBe("+9 min");
    expect(fmtLag(-14.4)).toBe("−14 min");
    expect(fmtLag(0)).toBe("0 min");
    expect(fmtLag(null)).toBe("no move");
  });
});

describe("spikeKey", () => {
  it("is stable across refetches", () => {
    expect(spikeKey({ kind: "jump", peak: "2025-12-10T15:08:00+00:00" })).toBe("jump@2025-12-10T15:08:00+00:00");
  });
});
