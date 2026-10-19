import { describe, expect, it } from "vitest";
import {
  buildChartEvents,
  formatPerShare,
  mergeMarkers,
  snapEventsToBars,
} from "@/lib/chart-events";
import type { DividendEvent } from "@/types/stock/dividends";
import type { CalendarEventOut } from "@/types/stock/events";

const TODAY = "2026-10-19";

function cal(type: string, event_date: string | null): CalendarEventOut {
  return {
    id: `${type}-${event_date}`,
    type,
    title_zh: "",
    title_en: "",
    event_date,
    state: "confirmed",
    source: "test",
  };
}

function div(over: Partial<DividendEvent>): DividendEvent {
  return {
    symbol: "2330",
    market: "TW",
    announced_date: "2026-01-01",
    fiscal_year: "2025",
    ...over,
  };
}

describe("buildChartEvents", () => {
  it("merges both feeds, newest first, and flags upcoming", () => {
    const events = buildChartEvents(
      [cal("agm", "2026-06-10"), cal("cash_capital_increase_rights", "2026-11-02")],
      [div({ cash_ex_dividend_date: "2026-09-15", cash_dividend: 4.5 })],
      TODAY,
    );
    expect(events.map((e) => [e.date, e.kind, e.upcoming])).toEqual([
      ["2026-11-02", "capitalIncrease", true],
      ["2026-09-15", "exDividend", false],
      ["2026-06-10", "agm", false],
    ]);
    expect(events[1]?.cash).toBe(4.5);
  });

  it("prefers the dividend row over the calendar's combined entry on the same date", () => {
    const events = buildChartEvents(
      [cal("dividend_ex_rights", "2026-09-15")],
      [div({ cash_ex_dividend_date: "2026-09-15", cash_dividend: 3 })],
      TODAY,
    );
    expect(events).toHaveLength(1);
    expect(events[0]?.kind).toBe("exDividend");
  });

  it("keeps the calendar entry when no dividend row covers the date", () => {
    const events = buildChartEvents([cal("dividend_ex_rights", "2026-09-15")], [], TODAY);
    expect(events.map((e) => e.kind)).toEqual(["exDividendRights"]);
  });

  it("dedupes duplicates by (date, kind) and skips unknown types and missing dates", () => {
    const events = buildChartEvents(
      [
        cal("agm", "2026-06-10"),
        cal("agm", "2026-06-10"),
        cal("earnings", "2026-05-01"),
        cal("agm", null),
      ],
      [],
      TODAY,
    );
    expect(events).toHaveLength(1);
  });

  it("keeps 除息 and 除權 on the same day as two events", () => {
    const events = buildChartEvents(
      [],
      [
        div({
          cash_ex_dividend_date: "2026-09-15",
          stock_ex_dividend_date: "2026-09-15",
          cash_dividend: 2,
          stock_dividend: 0.5,
        }),
      ],
      TODAY,
    );
    expect(events.map((e) => e.kind).sort()).toEqual(["exDividend", "exRights"]);
  });

  it("ignores zero amounts", () => {
    const [event] = buildChartEvents(
      [],
      [div({ cash_ex_dividend_date: "2026-09-15", cash_dividend: 0 })],
      TODAY,
    );
    expect(event?.cash).toBeUndefined();
  });
});

const ev = (date: string, kind: "agm" | "exDividend" | "capitalIncrease" = "agm") => ({
  date,
  kind,
  upcoming: false,
});

describe("snapEventsToBars", () => {
  const daily = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"];

  it("pins a daily event on its own bar", () => {
    expect(snapEventsToBars([ev("2026-09-15")], daily)).toEqual([
      { time: "2026-09-15", kinds: ["agm"] },
    ]);
  });

  it("snaps a non-trading day to the next existing bar", () => {
    expect(snapEventsToBars([ev("2026-09-13", "agm")], daily, "2026-09-01")).toEqual([
      { time: "2026-09-14", kinds: ["agm"] },
    ]);
  });

  it("snaps to the bucket's last-date bar for weekly bars", () => {
    const weekly = ["2026-09-04", "2026-09-11", "2026-09-18"];
    expect(snapEventsToBars([ev("2026-09-09")], weekly)).toEqual([
      { time: "2026-09-11", kinds: ["agm"] },
    ]);
  });

  it("snaps to the month-end bar for monthly bars", () => {
    const monthly = ["2026-07-31", "2026-08-31", "2026-09-30"];
    expect(snapEventsToBars([ev("2026-08-12")], monthly)).toEqual([
      { time: "2026-08-31", kinds: ["agm"] },
    ]);
  });

  it("drops events before the range and after the last bar", () => {
    expect(snapEventsToBars([ev("2026-08-01"), ev("2026-10-01")], daily)).toEqual([]);
  });

  it("uses rangeStart so an event in the first partial bucket is kept", () => {
    const weekly = ["2026-09-18"];
    expect(snapEventsToBars([ev("2026-09-14")], weekly, "2026-09-14")).toHaveLength(1);
  });

  it("groups several events on one bar and never repeats a kind", () => {
    const out = snapEventsToBars(
      [ev("2026-09-15", "exDividend"), ev("2026-09-15", "agm"), ev("2026-09-15", "agm")],
      daily,
    );
    expect(out).toEqual([{ time: "2026-09-15", kinds: ["exDividend", "agm"] }]);
  });

  it("returns nothing for an empty bar series", () => {
    expect(snapEventsToBars([ev("2026-09-15")], [])).toEqual([]);
  });
});

describe("formatPerShare", () => {
  it("rounds source float noise and trims trailing zeros", () => {
    expect(formatPerShare(7.00000137)).toBe("7");
    expect(formatPerShare(6.00003573)).toBe("6");
    expect(formatPerShare(5.00001118)).toBe("5");
    expect(formatPerShare(2.5)).toBe("2.5");
    expect(formatPerShare(0.35)).toBe("0.35");
  });
});

describe("mergeMarkers", () => {
  it("puts pattern and event markers into one time-ordered array", () => {
    const merged = mergeMarkers(
      [{ time: "2026-09-16", text: "pattern" }],
      [
        { time: "2026-09-14", text: "event" },
        { time: "2026-09-16", text: "event2" },
      ],
    );
    expect(merged.map((m) => m.text)).toEqual(["event", "pattern", "event2"]);
  });

  it("handles an empty group", () => {
    expect(mergeMarkers([], [{ time: "2026-09-14" }])).toHaveLength(1);
  });
});
