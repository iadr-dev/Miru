import { describe, expect, it } from "vitest";
import { squarifyGeneric } from "@/lib/treemap";

interface Item {
  id: string;
  size: number;
}

describe("squarifyGeneric", () => {
  it("returns one tile per item", () => {
    const items: Item[] = [
      { id: "a", size: 3_000_000 },
      { id: "b", size: 2_000_000 },
      { id: "c", size: 1_000_000 },
    ];
    const tiles = squarifyGeneric(items, (i) => i.size, 100, 60);
    expect(tiles).toHaveLength(3);
    expect(new Set(tiles.map((t) => t.item.id))).toEqual(new Set(["a", "b", "c"]));
  });

  it("gives a larger area to a larger size", () => {
    const items: Item[] = [
      { id: "big", size: 9_000_000 },
      { id: "small", size: 1_000_000 },
    ];
    const tiles = squarifyGeneric(items, (i) => i.size, 100, 60);
    const big = tiles.find((t) => t.item.id === "big");
    const small = tiles.find((t) => t.item.id === "small");
    expect(big).toBeDefined();
    expect(small).toBeDefined();
    const bigArea = (big?.width ?? 0) * (big?.height ?? 0);
    const smallArea = (small?.width ?? 0) * (small?.height ?? 0);
    expect(bigArea).toBeGreaterThan(smallArea);
  });

  it("tiles never overflow the layout box", () => {
    const items: Item[] = Array.from({ length: 8 }, (_, i) => ({
      id: `item-${i}`,
      size: (i + 1) * 100_000,
    }));
    const tiles = squarifyGeneric(items, (i) => i.size, 100, 60);
    for (const tile of tiles) {
      expect(tile.x).toBeGreaterThanOrEqual(-1e-6);
      expect(tile.y).toBeGreaterThanOrEqual(-1e-6);
      expect(tile.x + tile.width).toBeLessThanOrEqual(100 + 1e-6);
      expect(tile.y + tile.height).toBeLessThanOrEqual(60 + 1e-6);
    }
  });

  it("returns an empty array for no items", () => {
    expect(squarifyGeneric([], (i: Item) => i.size, 100, 60)).toEqual([]);
  });

  it("lays out a power-law-shaped distribution across both axes, not one stacked column", () => {
    const sizes = [
      535_234_669_075, 219_308_306_234, 118_413_024_584, 81_449_693_042, 71_198_341_015,
    ];
    const tail = Array.from({ length: 20 }, (_, i) => 5_000_000 - i * 100_000);
    const items: Item[] = [...sizes, ...tail].map((size, i) => ({ id: `item-${i}`, size }));
    const tiles = squarifyGeneric(items, (i) => i.size, 100, 60);
    const distinctX = new Set(tiles.map((t) => Math.round(t.x)));
    const distinctY = new Set(tiles.map((t) => Math.round(t.y)));
    expect(distinctX.size).toBeGreaterThan(1);
    expect(distinctY.size).toBeGreaterThan(1);
  });
});
