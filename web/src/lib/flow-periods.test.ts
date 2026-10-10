import { describe, expect, it } from "vitest";
import { type DailyFlowPoint, groupFlowByPeriod } from "@/lib/flow-periods";

const day = (trade_date: string, foreign: number, price: number | null = null): DailyFlowPoint => ({
  trade_date,
  foreign,
  trust: 1,
  dealer: -1,
  price,
});

const points = [
  day("2026-09-29", 10, 100), // Tue, week of 09-28
  day("2026-09-30", 20, 101), // Wed, same week, end of September
  day("2026-10-01", 30, 102), // Thu, same week, October
  day("2026-10-05", 40, 103), // Mon, next week
];

describe("groupFlowByPeriod", () => {
  it("keeps one bar per trading day", () => {
    expect(groupFlowByPeriod(points, "day").map((b) => b.label)).toEqual([
      "2026-09-29",
      "2026-09-30",
      "2026-10-01",
      "2026-10-05",
    ]);
  });

  it("sums Monday-start weeks and keeps the week's last close", () => {
    const bars = groupFlowByPeriod(points, "week");
    expect(bars.map((b) => b.label)).toEqual(["2026-09-28", "2026-10-05"]);
    expect(bars[0]).toMatchObject({ foreign: 60, trust: 3, dealer: -3, price: 102 });
  });

  it("sums calendar months", () => {
    const bars = groupFlowByPeriod(points, "month");
    expect(bars.map((b) => b.label)).toEqual(["2026-09", "2026-10"]);
    expect(bars[1]).toMatchObject({ foreign: 70, price: 103 });
  });
});
