import { describe, expect, it } from "vitest";
import { pivotByDate } from "@/components/market/sector-rotation-bump-chart";
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

describe("pivotByDate", () => {
  it("maps each industry's rank onto its own trade_date row", () => {
    const rows = [
      row({ trade_date: "2026-09-01", industry: "半導體業", rank: 1 }),
      row({ trade_date: "2026-09-01", industry: "金融保險業", rank: 2 }),
      row({ trade_date: "2026-09-02", industry: "半導體業", rank: 2 }),
      row({ trade_date: "2026-09-02", industry: "金融保險業", rank: 1 }),
    ];
    const points = pivotByDate(rows, ["2026-09-01", "2026-09-02"], ["半導體業", "金融保險業"]);

    expect(points).toEqual([
      { trade_date: "2026-09-01", 半導體業: 1, 金融保險業: 2 },
      { trade_date: "2026-09-02", 半導體業: 2, 金融保險業: 1 },
    ]);
  });

  it("fills a missing (date, industry) pair with null rather than omitting the key", () => {
    const rows = [row({ trade_date: "2026-09-01", industry: "半導體業", rank: 1 })];
    const points = pivotByDate(rows, ["2026-09-01"], ["半導體業", "金融保險業"]);

    expect(points).toEqual([{ trade_date: "2026-09-01", 半導體業: 1, 金融保險業: null }]);
  });

  it("returns one point per trade_date even when rows is empty", () => {
    const points = pivotByDate([], ["2026-09-01", "2026-09-02"], ["半導體業"]);

    expect(points).toEqual([
      { trade_date: "2026-09-01", 半導體業: null },
      { trade_date: "2026-09-02", 半導體業: null },
    ]);
  });
});
