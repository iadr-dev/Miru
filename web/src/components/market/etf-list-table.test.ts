import { describe, expect, it } from "vitest";
import { compareRows, DEFAULT_SORT_DIRECTION } from "@/components/market/etf-list-sort";
import type { EtfListRow } from "@/types/market";

function row(overrides: Partial<EtfListRow>): EtfListRow {
  return {
    symbol: "0050",
    market: "TW",
    name: "元大台灣50",
    close: 100,
    change_abs: 1,
    change_pct: 1,
    volume: 1000,
    turnover: 100_000,
    return_1w_pct: 1,
    distribution_yield_ttm_pct: 1,
    distribution_frequency: "季配",
    aum: 1_000_000,
    expense_ratio_pct_approx: 0.3,
    symbol_type: "etf",
    is_active_etf: false,
    ...overrides,
  };
}

describe("compareRows", () => {
  it("sorts numeric columns ascending", () => {
    const a = row({ symbol: "A", aum: 10 });
    const b = row({ symbol: "B", aum: 20 });
    expect(compareRows(a, b, "aum", "asc")).toBeLessThan(0);
    expect(compareRows(b, a, "aum", "asc")).toBeGreaterThan(0);
  });

  it("sorts numeric columns descending", () => {
    const a = row({ symbol: "A", aum: 10 });
    const b = row({ symbol: "B", aum: 20 });
    expect(compareRows(a, b, "aum", "desc")).toBeGreaterThan(0);
    expect(compareRows(b, a, "aum", "desc")).toBeLessThan(0);
  });

  it("always sorts null values last regardless of direction", () => {
    const withValue = row({ symbol: "A", aum: 10 });
    const withNull = row({ symbol: "B", aum: null });
    expect(compareRows(withValue, withNull, "aum", "asc")).toBeLessThan(0);
    expect(compareRows(withNull, withValue, "aum", "asc")).toBeGreaterThan(0);
    expect(compareRows(withValue, withNull, "aum", "desc")).toBeLessThan(0);
    expect(compareRows(withNull, withValue, "aum", "desc")).toBeGreaterThan(0);
  });

  it("treats two nulls as equal", () => {
    const a = row({ symbol: "A", aum: null });
    const b = row({ symbol: "B", aum: null });
    expect(compareRows(a, b, "aum", "asc")).toBe(0);
  });

  it("sorts name using zh-TW locale comparison", () => {
    const a = row({ symbol: "A", name: "元大台灣50" });
    const b = row({ symbol: "B", name: "國泰台灣加權" });
    const result = compareRows(a, b, "name", "asc");
    expect(result).toBe(a.name.localeCompare(b.name, "zh-TW"));
  });
});

describe("DEFAULT_SORT_DIRECTION", () => {
  it("defaults every numeric column to desc (largest-first)", () => {
    expect(DEFAULT_SORT_DIRECTION.aum).toBe("desc");
    expect(DEFAULT_SORT_DIRECTION.close).toBe("desc");
    expect(DEFAULT_SORT_DIRECTION.change_pct).toBe("desc");
    expect(DEFAULT_SORT_DIRECTION.turnover).toBe("desc");
    expect(DEFAULT_SORT_DIRECTION.return_1w_pct).toBe("desc");
    expect(DEFAULT_SORT_DIRECTION.distribution_yield_ttm_pct).toBe("desc");
    expect(DEFAULT_SORT_DIRECTION.expense_ratio_pct_approx).toBe("desc");
  });

  it("defaults name to asc (alphabetical)", () => {
    expect(DEFAULT_SORT_DIRECTION.name).toBe("asc");
  });
});
