import { describe, expect, it } from "vitest";
import {
  latestRankedByNetFlow,
  topInflowAndOutflow,
} from "@/components/market/sector-rotation-bar-chart";
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

describe("latestRankedByNetFlow", () => {
  it("filters rows to only the latest trade_date", () => {
    const rows = [
      row({ trade_date: "2026-09-01", industry: "半導體業", net_flow: 100 }),
      row({ trade_date: "2026-09-02", industry: "半導體業", net_flow: 200 }),
    ];
    const result = latestRankedByNetFlow(rows, ["2026-09-01", "2026-09-02"]);
    expect(result).toHaveLength(1);
    expect(result[0]?.net_flow).toBe(200);
  });

  it("sorts by net_flow descending", () => {
    const rows = [
      row({ trade_date: "2026-09-01", industry: "a", net_flow: -50 }),
      row({ trade_date: "2026-09-01", industry: "b", net_flow: 100 }),
      row({ trade_date: "2026-09-01", industry: "c", net_flow: 10 }),
    ];
    const result = latestRankedByNetFlow(rows, ["2026-09-01"]);
    expect(result.map((r) => r.industry)).toEqual(["b", "c", "a"]);
  });

  it("returns an empty array when tradeDates is empty", () => {
    const rows = [row({ trade_date: "2026-09-01" })];
    expect(latestRankedByNetFlow(rows, [])).toEqual([]);
  });
});

describe("topInflowAndOutflow", () => {
  it("includes both inflow and outflow rows even when one side dominates", () => {
    // 9 inflow rows, 1 outflow row — a plain top-10 slice would be all-inflow.
    const inflowRows = Array.from({ length: 9 }, (_, i) =>
      row({ industry: `in-${i}`, net_flow: 100 - i }),
    );
    const outflowRow = row({ industry: "out-0", net_flow: -5 });
    const sorted = [...inflowRows, outflowRow].sort((a, b) => b.net_flow - a.net_flow);

    const result = topInflowAndOutflow(sorted, 5);
    expect(result.some((r) => r.net_flow < 0)).toBe(true);
    expect(result.some((r) => r.net_flow > 0)).toBe(true);
  });

  it("never duplicates a row present in both slices when total rows <= 2*halfCount", () => {
    const rows = [
      row({ industry: "a", net_flow: 30 }),
      row({ industry: "b", net_flow: 10 }),
      row({ industry: "c", net_flow: -10 }),
    ];
    const result = topInflowAndOutflow(rows, 5);
    expect(new Set(result.map((r) => r.industry)).size).toBe(result.length);
    expect(result).toHaveLength(3);
  });
});
