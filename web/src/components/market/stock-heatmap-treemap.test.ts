import { describe, expect, it } from "vitest";
import { groupByIndustry, layoutNestedTiles } from "@/components/market/stock-heatmap-treemap";
import type { StockHeatmapRow } from "@/types/market";

function row(overrides: Partial<StockHeatmapRow>): StockHeatmapRow {
  return {
    symbol: "2330",
    stock_name: "台積電",
    industry: "半導體業",
    pct_change: 1.5,
    close: 600,
    turnover: 1_000_000,
    volume: 10_000,
    market_value: 10_000_000,
    ...overrides,
  };
}

describe("groupByIndustry", () => {
  it("groups rows by industry and sums their sizeBy metric", () => {
    const rows = [
      row({ symbol: "2330", industry: "半導體業", turnover: 3_000_000 }),
      row({ symbol: "2454", industry: "半導體業", turnover: 1_000_000 }),
      row({ symbol: "2317", industry: "電子工業", turnover: 500_000 }),
    ];
    const groups = groupByIndustry(rows, "turnover");
    const semi = groups.find((g) => g.industry === "半導體業");
    const electronics = groups.find((g) => g.industry === "電子工業");
    expect(semi?.totalSize).toBe(4_000_000);
    expect(semi?.stocks.map((s) => s.symbol)).toEqual(["2330", "2454"]);
    expect(electronics?.totalSize).toBe(500_000);
  });

  it("sorts each group's stocks descending by the sizeBy metric", () => {
    const rows = [
      row({ symbol: "small", industry: "a", turnover: 100 }),
      row({ symbol: "big", industry: "a", turnover: 900 }),
    ];
    const groups = groupByIndustry(rows, "turnover");
    expect(groups[0]?.stocks.map((s) => s.symbol)).toEqual(["big", "small"]);
  });
});

describe("layoutNestedTiles", () => {
  it("caps each industry region to at most 2 stock tiles", () => {
    const rows = [
      row({ symbol: "a", industry: "半導體業", turnover: 900 }),
      row({ symbol: "b", industry: "半導體業", turnover: 800 }),
      row({ symbol: "c", industry: "半導體業", turnover: 700 }),
    ];
    const tiles = layoutNestedTiles(rows, "turnover", undefined);
    const semiTiles = tiles.filter((t) => t.item.industry === "半導體業");
    expect(semiTiles).toHaveLength(2);
    expect(semiTiles.map((t) => t.item.symbol).sort()).toEqual(["a", "b"]);
  });

  it("respects maxIndustries by keeping only the biggest N industry regions", () => {
    const rows = [
      row({ symbol: "a", industry: "big-industry", turnover: 9_000_000 }),
      row({ symbol: "b", industry: "small-industry", turnover: 1_000 }),
    ];
    const tiles = layoutNestedTiles(rows, "turnover", 1);
    const industries = new Set(tiles.map((t) => t.item.industry));
    expect(industries).toEqual(new Set(["big-industry"]));
  });

  it("tiles never overflow the layout box", () => {
    const rows = Array.from({ length: 12 }, (_, i) =>
      row({
        symbol: `s${i}`,
        industry: `industry-${i % 4}`,
        turnover: (i + 1) * 100_000,
      }),
    );
    const tiles = layoutNestedTiles(rows, "turnover", undefined);
    for (const tile of tiles) {
      expect(tile.x).toBeGreaterThanOrEqual(-1e-6);
      expect(tile.y).toBeGreaterThanOrEqual(-1e-6);
      expect(tile.x + tile.width).toBeLessThanOrEqual(100 + 1e-6);
      expect(tile.y + tile.height).toBeLessThanOrEqual(60 + 1e-6);
    }
  });

  it("returns an empty array for no rows", () => {
    expect(layoutNestedTiles([], "turnover", undefined)).toEqual([]);
  });
});
