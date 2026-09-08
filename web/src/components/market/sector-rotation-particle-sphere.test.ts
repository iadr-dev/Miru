import { describe, expect, it } from "vitest";
import { layoutSphere } from "@/components/market/sector-rotation-particle-sphere";
import type { SectorRotationDayRow } from "@/types/market";

function row(overrides: Partial<SectorRotationDayRow>): SectorRotationDayRow {
  return {
    trade_date: "2026-09-01",
    industry: "半導體業",
    net_flow: 100,
    rank: 1,
    ...overrides,
  };
}

describe("layoutSphere", () => {
  it("returns one node per row", () => {
    const rows = [row({ industry: "a" }), row({ industry: "b" }), row({ industry: "c" })];
    const nodes = layoutSphere(rows);
    expect(nodes).toHaveLength(3);
    expect(new Set(nodes.map((n) => n.row.industry))).toEqual(new Set(["a", "b", "c"]));
  });

  it("gives a larger size to a larger |net_flow|", () => {
    const rows = [
      row({ industry: "big", net_flow: 900 }),
      row({ industry: "small", net_flow: 10 }),
    ];
    const nodes = layoutSphere(rows);
    const big = nodes.find((n) => n.row.industry === "big");
    const small = nodes.find((n) => n.row.industry === "small");
    expect(big?.size).toBeGreaterThan(small?.size ?? 0);
  });

  it("returns an empty array for no rows", () => {
    expect(layoutSphere([])).toEqual([]);
  });
});
