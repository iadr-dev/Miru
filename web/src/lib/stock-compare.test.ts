import { describe, expect, it } from "vitest";
import {
  addCompareSymbol,
  COMPARE_MAX_SYMBOLS,
  COMPARE_RANGE_DAYS,
  COMPARE_RANGES,
  canAddCompareSymbol,
  compareHref,
  parseCompareSymbols,
  parseTechnicalSub,
  pickPeers,
  rebaseToCommonStart,
  removeCompareSymbol,
  serializeCompareSymbols,
} from "@/lib/stock-compare";
import type { ThemeMember } from "@/types/theme";

function bars(entries: readonly [string, number][]): { date: string; close: number }[] {
  return entries.map(([date, close]) => ({ date, close }));
}

describe("rebaseToCommonStart", () => {
  it("rebases each line to 0% on the shared first date", () => {
    const result = rebaseToCommonStart([
      {
        symbol: "2330",
        bars: bars([
          ["2026-01-02", 1000],
          ["2026-01-03", 1100],
          ["2026-01-06", 900],
        ]),
      },
      {
        symbol: "2454",
        bars: bars([
          ["2026-01-02", 50],
          ["2026-01-03", 50],
          ["2026-01-06", 75],
        ]),
      },
    ]);
    expect(result.baseDate).toBe("2026-01-02");
    expect(result.limitedBy).toEqual([]);
    expect(result.rows.map((row) => row.date)).toEqual(["2026-01-02", "2026-01-03", "2026-01-06"]);
    expect(result.rows[0]?.pct).toEqual({ "2330": 0, "2454": 0 });
    expect(result.rows[1]?.pct["2330"]).toBeCloseTo(10);
    expect(result.rows[2]?.pct["2330"]).toBeCloseTo(-10);
    expect(result.rows[2]?.pct["2454"]).toBeCloseTo(50);
    expect(result.series.map((s) => [s.symbol, s.endPct, s.endDate])).toEqual([
      ["2330", expect.closeTo(-10), "2026-01-06"],
      ["2454", expect.closeTo(50), "2026-01-06"],
    ]);
  });

  it("starts at the latest first date when one symbol has a shorter history", () => {
    const result = rebaseToCommonStart([
      {
        symbol: "2330",
        bars: bars([
          ["2026-01-02", 100],
          ["2026-01-03", 110],
          ["2026-01-06", 121],
        ]),
      },
      {
        symbol: "6999",
        bars: bars([
          ["2026-01-03", 20],
          ["2026-01-06", 30],
        ]),
      },
    ]);
    expect(result.baseDate).toBe("2026-01-03");
    expect(result.limitedBy).toEqual(["6999"]);
    expect(result.rows.map((row) => row.date)).toEqual(["2026-01-03", "2026-01-06"]);
    // 2330 is rebased on 2026-01-03 (110), not on its own earlier first bar.
    expect(result.rows[1]?.pct["2330"]).toBeCloseTo(10);
    expect(result.rows[1]?.pct["6999"]).toBeCloseTo(50);
  });

  it("leaves a gap (null) where a symbol did not trade, without filling it", () => {
    const result = rebaseToCommonStart([
      {
        symbol: "2330",
        bars: bars([
          ["2026-01-02", 100],
          ["2026-01-03", 105],
          ["2026-01-06", 110],
        ]),
      },
      {
        symbol: "2454",
        bars: bars([
          ["2026-01-02", 10],
          ["2026-01-06", 12],
        ]),
      },
    ]);
    expect(result.rows.map((row) => row.date)).toEqual(["2026-01-02", "2026-01-03", "2026-01-06"]);
    expect(result.rows[1]?.pct["2454"]).toBeNull();
    expect(result.rows[2]?.pct["2454"]).toBeCloseTo(20);
  });

  it("bases a symbol on its last close before the base date when it did not trade that day", () => {
    const result = rebaseToCommonStart([
      {
        symbol: "2330",
        bars: bars([
          ["2026-01-02", 100],
          ["2026-01-06", 120],
        ]),
      },
      {
        symbol: "2454",
        bars: bars([
          ["2026-01-01", 10],
          ["2026-01-02", 10],
          ["2026-01-05", 11],
          ["2026-01-06", 12],
        ]),
      },
      {
        symbol: "6999",
        bars: bars([
          ["2026-01-05", 40],
          ["2026-01-06", 44],
        ]),
      },
    ]);
    expect(result.baseDate).toBe("2026-01-05");
    // 2330 has no bar on 01-05: it is based on its 01-02 close.
    expect(result.rows[0]?.pct["2330"]).toBeNull();
    expect(result.rows[1]?.pct["2330"]).toBeCloseTo(20);
    expect(result.rows[0]?.pct["2454"]).toBe(0);
    expect(result.rows[1]?.pct["2454"]).toBeCloseTo((12 / 11 - 1) * 100);
  });

  it("lists a symbol with no bars as missing and draws the rest", () => {
    const result = rebaseToCommonStart([
      {
        symbol: "2330",
        bars: bars([
          ["2026-01-02", 100],
          ["2026-01-03", 101],
        ]),
      },
      { symbol: "9999", bars: [] },
    ]);
    expect(result.missing).toEqual(["9999"]);
    expect(result.baseDate).toBe("2026-01-02");
    expect(result.series.find((s) => s.symbol === "9999")).toEqual({
      symbol: "9999",
      endPct: null,
      endDate: null,
    });
  });

  it("ignores non-positive closes and sorts unordered input", () => {
    const result = rebaseToCommonStart([
      {
        symbol: "2330",
        bars: bars([
          ["2026-01-06", 110],
          ["2026-01-02", 100],
          ["2026-01-03", 0],
        ]),
      },
    ]);
    expect(result.rows.map((row) => row.date)).toEqual(["2026-01-02", "2026-01-06"]);
    expect(result.series[0]?.endPct).toBeCloseTo(10);
  });

  it("returns an empty result when no symbol has data", () => {
    const result = rebaseToCommonStart([{ symbol: "2330", bars: [] }]);
    expect(result.baseDate).toBeNull();
    expect(result.rows).toEqual([]);
    expect(result.missing).toEqual(["2330"]);
  });
});

describe("compare URL state", () => {
  it("parses ?sub= leniently", () => {
    expect(parseTechnicalSub("compare")).toBe("compare");
    expect(parseTechnicalSub("nope")).toBe("chart");
    expect(parseTechnicalSub(null)).toBe("chart");
  });

  it("parses ?cmp= into valid, unique, capped codes", () => {
    expect(parseCompareSymbols("2330,2454")).toEqual(["2330", "2454"]);
    expect(parseCompareSymbols(" 2330 ,2330,abc,00631l")).toEqual(["2330", "00631L"]);
    expect(parseCompareSymbols("1101,1102,1103,1104,1105,1106,1107")).toHaveLength(
      COMPARE_MAX_SYMBOLS,
    );
    expect(parseCompareSymbols("")).toEqual([]);
    expect(parseCompareSymbols("<script>")).toBeNull();
    expect(parseCompareSymbols(null)).toBeNull();
  });

  it("round-trips through serialize", () => {
    expect(serializeCompareSymbols(["2330", "2454"])).toBe("2330,2454");
    expect(serializeCompareSymbols([])).toBe("");
  });

  it("builds a deep link from the first five codes", () => {
    expect(compareHref(["2330", "2454", "2317", "2303", "2412", "1101"])).toBe(
      "/stock/2330?tab=technical&sub=compare&cmp=2330%2C2454%2C2317%2C2303%2C2412",
    );
    expect(compareHref([])).toBeNull();
    expect(compareHref(["AAPL"])).toBeNull();
  });
});

describe("symbol limit", () => {
  const five = ["2330", "2454", "2317", "2303", "2412"];

  it("refuses a sixth symbol, a duplicate and a malformed code", () => {
    expect(canAddCompareSymbol(five, "1101")).toBe(false);
    expect(addCompareSymbol(five, "1101")).toBe(five);
    expect(canAddCompareSymbol(["2330"], "2330")).toBe(false);
    expect(canAddCompareSymbol(["2330"], "AAPL")).toBe(false);
  });

  it("adds below the cap and removes by code", () => {
    expect(addCompareSymbol(["2330"], "2454")).toEqual(["2330", "2454"]);
    expect(removeCompareSymbol(five, "2317")).toEqual(["2330", "2454", "2303", "2412"]);
    expect(canAddCompareSymbol(removeCompareSymbol(five, "2317"), "1101")).toBe(true);
  });
});

describe("ranges", () => {
  it("every range fits the backend's 260-day cap", () => {
    for (const range of COMPARE_RANGES) {
      expect(COMPARE_RANGE_DAYS[range]).toBeLessThanOrEqual(260);
      expect(COMPARE_RANGE_DAYS[range]).toBeGreaterThanOrEqual(2);
    }
  });
});

describe("pickPeers", () => {
  function member(symbol: string, confidence: number, market = "TW"): ThemeMember {
    return { symbol, market, sub_industry: "", confidence, source: "t", position: null };
  }

  it("takes the most confident other TW stocks, skipping the current symbol and ETFs", () => {
    const members = [
      member("2330", 1),
      member("0050", 0.99),
      member("2454", 0.9),
      member("AAPL", 0.95, "US"),
      member("3034", 0.5),
      member("2303", 0.7),
    ];
    expect(pickPeers(members, "2330", 2)).toEqual(["2454", "2303"]);
  });

  it("returns nothing when there are no peers", () => {
    expect(pickPeers([member("2330", 1)], "2330", 2)).toEqual([]);
  });
});
