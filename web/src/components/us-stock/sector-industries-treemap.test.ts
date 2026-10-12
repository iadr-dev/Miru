import { describe, expect, it } from "vitest";
import { squarifyByWeight } from "@/components/us-stock/sector-industries-treemap";
import type { SectorIndustryWeightRow } from "@/types/stock/profile";

function row(overrides: Partial<SectorIndustryWeightRow>): SectorIndustryWeightRow {
  return {
    market: "US",
    sector_key: "technology",
    industry_key: "semiconductors",
    name: "Semiconductors",
    market_weight: 0.38,
    ...overrides,
  };
}

describe("squarifyByWeight", () => {
  it("returns one tile per row", () => {
    const rows = [
      row({ industry_key: "semiconductors", market_weight: 0.38 }),
      row({ industry_key: "software-infrastructure", market_weight: 0.21 }),
      row({ industry_key: "consumer-electronics", market_weight: 0.17 }),
    ];
    const tiles = squarifyByWeight(rows, 100, 60);
    expect(tiles).toHaveLength(3);
    expect(new Set(tiles.map((t) => t.row.industry_key))).toEqual(
      new Set(["semiconductors", "software-infrastructure", "consumer-electronics"]),
    );
  });

  it("gives a larger area to a larger market_weight", () => {
    const rows = [
      row({ industry_key: "big", market_weight: 0.7 }),
      row({ industry_key: "small", market_weight: 0.05 }),
    ];
    const tiles = squarifyByWeight(rows, 100, 60);
    const big = tiles.find((t) => t.row.industry_key === "big");
    const small = tiles.find((t) => t.row.industry_key === "small");
    expect(big).toBeDefined();
    expect(small).toBeDefined();
    const bigArea = (big?.width ?? 0) * (big?.height ?? 0);
    const smallArea = (small?.width ?? 0) * (small?.height ?? 0);
    expect(bigArea).toBeGreaterThan(smallArea);
  });

  it("returns no tiles for an empty row list", () => {
    expect(squarifyByWeight([], 100, 60)).toHaveLength(0);
  });

  it("tiles never overflow the layout box", () => {
    const rows = Array.from({ length: 8 }, (_, i) =>
      row({ industry_key: `industry-${i}`, market_weight: (i + 1) * 0.05 }),
    );
    const tiles = squarifyByWeight(rows, 100, 60);
    for (const tile of tiles) {
      expect(tile.x).toBeGreaterThanOrEqual(-0.001);
      expect(tile.y).toBeGreaterThanOrEqual(-0.001);
      expect(tile.x + tile.width).toBeLessThanOrEqual(100.001);
      expect(tile.y + tile.height).toBeLessThanOrEqual(60.001);
    }
  });
});
