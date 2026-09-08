import { describe, expect, it } from "vitest";
import { squarify } from "@/components/market/sector-heatmap-treemap";
import type { SectorHeatmapRow } from "@/types/market";

function row(overrides: Partial<SectorHeatmapRow>): SectorHeatmapRow {
  return {
    industry: "半導體業",
    avg_pct_change: 1.5,
    stock_count: 10,
    advancing_count: 6,
    declining_count: 4,
    total_turnover: 1_000_000,
    weighted_by_turnover: true,
    total_volume: 500_000,
    total_market_value: 10_000_000,
    ...overrides,
  };
}

describe("squarify", () => {
  it("returns one tile per row", () => {
    const rows = [
      row({ industry: "半導體業", total_turnover: 3_000_000 }),
      row({ industry: "金融保險業", total_turnover: 2_000_000 }),
      row({ industry: "塑膠工業", total_turnover: 1_000_000 }),
    ];
    const tiles = squarify(rows, 100, 60);
    expect(tiles).toHaveLength(3);
    expect(new Set(tiles.map((t) => t.row.industry))).toEqual(
      new Set(["半導體業", "金融保險業", "塑膠工業"]),
    );
  });

  it("gives a larger area to a larger total_turnover", () => {
    const rows = [
      row({ industry: "big", total_turnover: 9_000_000 }),
      row({ industry: "small", total_turnover: 1_000_000 }),
    ];
    const tiles = squarify(rows, 100, 60);
    const big = tiles.find((t) => t.row.industry === "big");
    const small = tiles.find((t) => t.row.industry === "small");
    expect(big).toBeDefined();
    expect(small).toBeDefined();
    const bigArea = (big?.width ?? 0) * (big?.height ?? 0);
    const smallArea = (small?.width ?? 0) * (small?.height ?? 0);
    expect(bigArea).toBeGreaterThan(smallArea);
  });

  it("falls back to stock_count when total_turnover is null", () => {
    const rows = [
      row({ industry: "a", total_turnover: null, stock_count: 20 }),
      row({ industry: "b", total_turnover: null, stock_count: 5 }),
    ];
    const tiles = squarify(rows, 100, 60);
    expect(tiles).toHaveLength(2);
  });

  it("tiles never overflow the layout box", () => {
    const rows = Array.from({ length: 8 }, (_, i) =>
      row({ industry: `industry-${i}`, total_turnover: (i + 1) * 100_000 }),
    );
    const tiles = squarify(rows, 100, 60);
    for (const tile of tiles) {
      expect(tile.x).toBeGreaterThanOrEqual(-1e-6);
      expect(tile.y).toBeGreaterThanOrEqual(-1e-6);
      expect(tile.x + tile.width).toBeLessThanOrEqual(100 + 1e-6);
      expect(tile.y + tile.height).toBeLessThanOrEqual(60 + 1e-6);
    }
  });

  it("returns an empty array for no rows", () => {
    expect(squarify([], 100, 60)).toEqual([]);
  });

  it("lays out a power-law-shaped distribution across both axes, not one stacked column", () => {
    // Regression guard: a naive fixed-threshold banding heuristic (the first implementation)
    // degenerated into a single full-height column for real turnover data shaped like this —
    // one dominant industry plus a long tail of much smaller ones.
    const sizes = [
      535_234_669_075, 219_308_306_234, 118_413_024_584, 81_449_693_042, 71_198_341_015,
    ];
    const tail = Array.from({ length: 20 }, (_, i) => 5_000_000 - i * 100_000);
    const rows = [...sizes, ...tail].map((size, i) =>
      row({ industry: `industry-${i}`, total_turnover: size }),
    );
    const tiles = squarify(rows, 100, 60);
    const distinctX = new Set(tiles.map((t) => Math.round(t.x)));
    const distinctY = new Set(tiles.map((t) => Math.round(t.y)));
    expect(distinctX.size).toBeGreaterThan(1);
    expect(distinctY.size).toBeGreaterThan(1);
  });
});
