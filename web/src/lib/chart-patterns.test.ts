import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import {
  buildPatternLines,
  buildPatternPivotMarkers,
  CHART_PATTERN_DRAW_LIMIT,
  patternKey,
  patternLevels,
  patternStroke,
  patternsToDraw,
  snapToBarDate,
  sortPatternsNewestFirst,
} from "@/lib/chart-patterns";
import type { ChartPatternHit } from "@/types/stock/technical";
import en from "../../messages/en.json";
import zhTW from "../../messages/zh-TW.json";

const anchor = (trade_date: string, price: number, bar_index = 0) => ({
  bar_index,
  trade_date,
  price,
});

function hit(overrides: Partial<ChartPatternHit> = {}): ChartPatternHit {
  return {
    pattern: "double_top",
    family: "reversal_top",
    status: "forming",
    pivots: [
      { ...anchor("2026-01-05", 110), role: "peak" },
      { ...anchor("2026-01-20", 100), role: "trough" },
      { ...anchor("2026-02-03", 109), role: "peak" },
    ],
    lines: [{ name: "neckline", start: anchor("2026-01-05", 100), end: anchor("2026-02-20", 100) }],
    breakout_level: 100,
    broken_side: null,
    height: 10,
    window_start: anchor("2026-01-05", 110),
    window_end: anchor("2026-02-03", 109),
    confirmed_at: null,
    failed_at: null,
    ...overrides,
  };
}

const DAYS = ["2026-01-05", "2026-01-12", "2026-01-20", "2026-02-03", "2026-02-20"];

describe("patternStroke", () => {
  it("colours by family and dashes by status, with failed shapes muted and dotted", () => {
    expect(patternStroke(hit({ status: "confirmed" }))).toEqual({
      token: "var(--chart-5)",
      lineStyle: 0,
    });
    expect(patternStroke(hit({ status: "forming" })).lineStyle).toBe(2);
    expect(patternStroke(hit({ status: "failed" }))).toEqual({
      token: "var(--text-muted)",
      lineStyle: 1,
    });
    expect(patternStroke(hit({ family: "consolidation" })).token).toBe("var(--chart-2)");
  });

  it("never uses the signed price tokens", () => {
    for (const family of ["reversal_top", "reversal_bottom", "consolidation"] as const) {
      for (const status of ["forming", "confirmed", "failed"] as const) {
        expect(patternStroke({ family, status }).token).not.toMatch(/--up|--down/);
      }
    }
  });
});

describe("patternsToDraw", () => {
  const confirmed = hit({ status: "confirmed" });
  const failed = hit({
    status: "failed",
    window_start: anchor("2025-06-01", 90),
    window_end: anchor("2025-07-01", 90),
  });

  it("draws nothing with the toggle off and nothing selected", () => {
    expect(patternsToDraw([confirmed, failed], false, null)).toEqual([]);
  });

  it("draws forming and confirmed hits with the toggle on, leaving failed ones to the list", () => {
    expect(patternsToDraw([confirmed, failed], true, null)).toEqual([confirmed]);
  });

  it("always draws the selected hit, even failed or with the toggle off", () => {
    expect(patternsToDraw([confirmed, failed], false, patternKey(failed))).toEqual([failed]);
    expect(patternsToDraw([confirmed, failed], true, patternKey(failed))).toEqual([
      confirmed,
      failed,
    ]);
  });

  it("caps the drawn hits at the most recent few", () => {
    const many = Array.from({ length: 10 }, (_, i) =>
      hit({
        window_start: anchor(`2026-01-${String(i + 1).padStart(2, "0")}`, 1),
        window_end: anchor(`2026-02-${String(i + 1).padStart(2, "0")}`, 1),
      }),
    );
    const drawn = patternsToDraw(many, true, null);
    expect(drawn).toHaveLength(CHART_PATTERN_DRAW_LIMIT);
    expect(drawn[0]?.window_end.trade_date).toBe("2026-02-10");
  });
});

describe("sortPatternsNewestFirst", () => {
  it("orders by the date the last pivot formed, newest first, without mutating the input", () => {
    const older = hit({ window_end: anchor("2025-12-01", 1) });
    const newer = hit({ window_end: anchor("2026-03-01", 1) });
    const input = [older, newer];
    expect(sortPatternsNewestFirst(input)).toEqual([newer, older]);
    expect(input).toEqual([older, newer]);
  });
});

describe("snapToBarDate", () => {
  it("snaps to the first bar on or after the date and clamps past the last bar", () => {
    expect(snapToBarDate("2026-01-12", DAYS)).toBe("2026-01-12");
    expect(snapToBarDate("2026-01-13", DAYS)).toBe("2026-01-20");
    expect(snapToBarDate("2027-01-01", DAYS)).toBe("2026-02-20");
    expect(snapToBarDate("2026-01-01", [])).toBeUndefined();
  });
});

describe("buildPatternLines", () => {
  it("returns the neckline as a two-point series with the hit's levels", () => {
    const [line] = buildPatternLines(hit(), DAYS, false);
    expect(line?.points).toEqual([
      { time: "2026-01-05", value: 100 },
      { time: "2026-02-20", value: 100 },
    ]);
    expect(line?.lineWidth).toBe(2);
    expect(line?.lineStyle).toBe(2);
  });

  it("draws trendlines thinner than a neckline and the highlighted hit heaviest", () => {
    const box = hit({
      pattern: "rectangle",
      family: "consolidation",
      lines: [
        { name: "upper", start: anchor("2026-01-05", 110), end: anchor("2026-02-20", 110) },
        { name: "lower", start: anchor("2026-01-05", 100), end: anchor("2026-02-20", 100) },
      ],
    });
    expect(buildPatternLines(box, DAYS, false).map((l) => l.lineWidth)).toEqual([1, 1]);
    expect(buildPatternLines(box, DAYS, true).map((l) => l.lineWidth)).toEqual([3, 3]);
  });

  it("snaps onto the displayed bars and skips a line that collapses to one bar", () => {
    const weekly = ["2026-01-09", "2026-02-20"];
    const [line] = buildPatternLines(hit(), weekly, false);
    expect(line?.points[0].time).toBe("2026-01-09");
    expect(
      buildPatternLines(hit(), ["2026-02-20"], false),
      "both ends snap to the same bar",
    ).toEqual([]);
  });
});

describe("buildPatternPivotMarkers", () => {
  const bars = [
    { trade_date: "2026-01-05", high: 110, low: 105 },
    { trade_date: "2026-01-20", high: 104, low: 100 },
    { trade_date: "2026-02-03", high: 109, low: 104 },
  ];

  it("pins peaks above their bar and troughs below, labelling only the first pivot", () => {
    const markers = buildPatternPivotMarkers(hit(), bars);
    expect(markers.map((m) => m.position)).toEqual(["aboveBar", "belowBar", "aboveBar"]);
    expect(markers.map((m) => m.labelled)).toEqual([true, false, false]);
  });

  it("drops a pivot when there are no bars to put it on", () => {
    expect(buildPatternPivotMarkers(hit(), [])).toEqual([]);
  });
});

describe("patternLevels", () => {
  it("reports the neckline for a reversal and both boundaries for a consolidation", () => {
    expect(patternLevels(hit())).toEqual({ neckline: 100, upper: null, lower: null, height: 10 });
    const box = hit({
      breakout_level: null,
      lines: [
        { name: "upper", start: anchor("2026-01-05", 110), end: anchor("2026-02-20", 111) },
        { name: "lower", start: anchor("2026-01-05", 100), end: anchor("2026-02-20", 101) },
      ],
    });
    expect(patternLevels(box)).toEqual({ neckline: null, upper: 111, lower: 101, height: 10 });
  });
});

describe("chart-pattern copy and source governance", () => {
  const zh = zhTW.stock.technical.chartPatterns;
  const enBlock = en.stock.technical.chartPatterns;

  function leafKeys(value: unknown, prefix = ""): string[] {
    if (typeof value !== "object" || value === null) return [prefix];
    return Object.entries(value).flatMap(([key, child]) =>
      leafKeys(child, prefix ? `${prefix}.${key}` : key),
    );
  }

  it("keeps identical keys in zh-TW and en, including the toggle label", () => {
    expect(leafKeys(zh).sort()).toEqual(leafKeys(enBlock).sort());
    expect(zhTW.stock.technical.structureChartPatterns).toBeTruthy();
    expect(en.stock.technical.structureChartPatterns).toBeTruthy();
  });

  it("names a label for every pattern, status and family the backend can return", () => {
    for (const id of [
      "double_top",
      "double_bottom",
      "head_shoulders_top",
      "head_shoulders_bottom",
      "ascending_triangle",
      "descending_triangle",
      "symmetrical_triangle",
      "rising_wedge",
      "falling_wedge",
      "rectangle",
    ]) {
      expect(zh.pattern).toHaveProperty(id);
      expect(zh.short).toHaveProperty(id);
      expect(enBlock.pattern).toHaveProperty(id);
    }
    expect(Object.keys(zh.status).sort()).toEqual(["confirmed", "failed", "forming"]);
    expect(Object.keys(zh.family).sort()).toEqual([
      "consolidation",
      "reversal_bottom",
      "reversal_top",
    ]);
  });

  it("uses no trading language and no direction words", () => {
    const banned =
      /買進|賣出|買賣|目標價|進場|出場|停損|明牌|飆股|看多|看空|\b(buy|sell|hold|bullish|bearish|target|stop[- ]loss|entry)\b/i;
    expect(JSON.stringify(zh)).not.toMatch(banned);
    expect(JSON.stringify(enBlock)).not.toMatch(banned);
  });

  it("keeps the pattern source off the signed up/down tokens", () => {
    const here = path.resolve(__dirname, "..");
    for (const file of [
      "lib/chart-patterns.ts",
      "components/stock/technical/chart-pattern-list.tsx",
    ]) {
      expect(readFileSync(path.join(here, file), "utf8"), file).not.toMatch(
        /--up|--down|SignedChange|linear-gradient/,
      );
    }
  });
});
