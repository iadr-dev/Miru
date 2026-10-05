import { describe, expect, it } from "vitest";
import { buildBatch, fillBlankRows, type PerStockRow, quickFillLevel } from "./batch";

const row = (over: Partial<PerStockRow>): PerStockRow => ({
  symbol: "2330",
  market: "TW",
  close: 2475,
  comparator: "above",
  level: "",
  ...over,
});

describe("quickFillLevel", () => {
  it("measures from each stock's own close, so different prices get different levels", () => {
    expect(quickFillLevel(2475, "above", 5)).toBe("2598.75");
    expect(quickFillLevel(185.5, "above", 5)).toBe("194.78");
  });

  it("goes below the close for a 'below' level", () => {
    expect(quickFillLevel(100, "below", 5)).toBe("95");
  });

  it("has nothing to offer without a usable close", () => {
    expect(quickFillLevel(null, "above", 5)).toBe("");
    expect(quickFillLevel(0, "above", 5)).toBe("");
  });
});

describe("fillBlankRows", () => {
  it("fills only blank rows and keeps what the user already typed", () => {
    const rows = [row({ symbol: "2330", level: "2600" }), row({ symbol: "2454", close: 1420 })];
    const filled = fillBlankRows(rows, "below", 10);
    expect(filled[0]?.level).toBe("2600");
    expect(filled[0]?.comparator).toBe("above");
    expect(filled[1]?.level).toBe("1278");
    expect(filled[1]?.comparator).toBe("below");
  });
});

describe("buildBatch", () => {
  it("creates one close_price rule per filled row with its own level and skips blanks", () => {
    const result = buildBatch([
      row({ symbol: "2330", level: "2600" }),
      row({ symbol: "2454", close: 1420, comparator: "below", level: "1350.5" }),
      row({ symbol: "3711", close: 185.5 }),
    ]);
    expect(result).toEqual({
      ok: true,
      skipped: 1,
      items: [
        {
          symbol: "2330",
          market: "TW",
          condition: { kind: "close_price", comparator: "above", value: 2600 },
        },
        {
          symbol: "2454",
          market: "TW",
          condition: { kind: "close_price", comparator: "below", value: 1350.5 },
        },
      ],
    });
  });

  it("names the stock whose level is not a positive number", () => {
    const result = buildBatch([
      row({ symbol: "2330", level: "abc" }),
      row({ symbol: "2454", level: "0" }),
    ]);
    expect(result).toEqual({ ok: false, reason: "invalid", badSymbols: ["2330", "2454"] });
  });

  it("refuses an empty sheet", () => {
    expect(buildBatch([row({})])).toEqual({ ok: false, reason: "nothing", badSymbols: [] });
  });
});
