import { describe, expect, it } from "vitest";

import { parseQuery, queryString } from "./url";

describe("parseQuery", () => {
  it("defaults to an empty ticker and 30 days", () => {
    expect(parseQuery("")).toEqual({ ticker: "", days: 30 });
    expect(parseQuery("?x=1")).toEqual({ ticker: "", days: 30 });
  });
  it("uppercases the ticker and accepts only 7 or 30 days", () => {
    expect(parseQuery("?t=nvda&days=7")).toEqual({ ticker: "NVDA", days: 7 });
    expect(parseQuery("?t=NVDA&days=10")).toEqual({ ticker: "NVDA", days: 30 });
    expect(parseQuery("?t=%20tsla%20")).toEqual({ ticker: "TSLA", days: 30 });
  });
  it("rejects a ticker that is not a plain symbol", () => {
    expect(parseQuery("?t=<script>")).toEqual({ ticker: "", days: 30 });
  });
});

describe("queryString", () => {
  it("round-trips", () => {
    expect(parseQuery(queryString("NVDA", 7))).toEqual({ ticker: "NVDA", days: 7 });
    expect(queryString("", 30)).toBe("");
    expect(queryString("BRK.B", 30)).toBe("?t=BRK.B");
  });
});
