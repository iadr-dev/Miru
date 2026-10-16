import { describe, expect, it } from "vitest";
import { clampRecessionBands, sliceToRange, toTimestamp, yearsBefore } from "@/lib/us-macro-chart";
import {
  currentWeekBounds,
  formatMacroChange,
  formatMacroValue,
  isOlderThanUsual,
} from "@/lib/us-macro-format";

describe("formatMacroValue", () => {
  it("formats by unit kind", () => {
    expect(formatMacroValue(4.3, "percent", "zh-TW")).toBe("4.30%");
    expect(formatMacroValue(321.456, "index", "zh-TW")).toBe("321.5");
    expect(formatMacroValue(159_800, "count", "zh-TW")).toBe("159,800");
    expect(formatMacroValue(7_450_123, "level", "zh-TW")).toBe("7,450,123");
    expect(formatMacroValue(12.5, "level", "zh-TW")).toBe("12.50");
    expect(formatMacroValue(null, "percent", "zh-TW")).toBe("—");
  });
});

describe("formatMacroChange", () => {
  it("is plain signed text, with no arrow glyph", () => {
    expect(formatMacroChange(0.2, "percent", "zh-TW")).toBe("+0.20");
    expect(formatMacroChange(-1.5, "index", "zh-TW")).toBe("-1.5");
    expect(formatMacroChange(0, "percent", "zh-TW")).toBe("0.00");
    expect(formatMacroChange(0.001, "percent", "zh-TW")).toBe("0.00");
    expect(formatMacroChange(null, "percent", "zh-TW")).toBe("—");
    expect(formatMacroChange(0.2, "percent", "zh-TW")).not.toMatch(/[▲▼]/);
  });
});

describe("isOlderThanUsual", () => {
  it("compares the last observation to the data's own as-of date by frequency", () => {
    expect(isOlderThanUsual("2026-09-01", "monthly", "2026-10-15")).toBe(false);
    expect(isOlderThanUsual("2026-06-01", "monthly", "2026-10-15")).toBe(true);
    expect(isOlderThanUsual("2026-10-01", "daily", "2026-10-15")).toBe(true);
    expect(isOlderThanUsual(null, "daily", "2026-10-15")).toBe(false);
    expect(isOlderThanUsual("2026-01-01", "unknown", "2026-10-15")).toBe(false);
  });
});

describe("currentWeekBounds", () => {
  it("runs Monday to Sunday", () => {
    expect(currentWeekBounds(new Date(2026, 9, 16))).toEqual({
      start: "2026-10-12",
      end: "2026-10-18",
    });
    expect(currentWeekBounds(new Date(2026, 9, 18))).toEqual({
      start: "2026-10-12",
      end: "2026-10-18",
    });
  });
});

describe("chart helpers", () => {
  it("slices to a window ending at the newest point", () => {
    const points = [
      { date: "2023-01-01", value: 1 },
      { date: "2025-05-01", value: 2 },
      { date: "2026-06-01", value: 3 },
    ];
    expect(sliceToRange(points, 1).map((p) => p.value)).toEqual([3]);
    expect(sliceToRange(points, 3).map((p) => p.value)).toEqual([2, 3]);
    expect(sliceToRange([], 1)).toEqual([]);
    expect(yearsBefore(new Date("2026-10-16T00:00:00Z"), 5)).toBe("2021-10-16");
  });

  it("clamps recession bands to the window and extends an ongoing band to the last point", () => {
    const start = toTimestamp("2026-01-01");
    const end = toTimestamp("2026-12-01");
    const bands = clampRecessionBands(
      [
        { start: "2020-02-01", end: "2020-04-30", ongoing: false },
        { start: "2025-11-01", end: "2026-02-01", ongoing: false },
        { start: "2026-09-01", end: "2026-09-01", ongoing: true },
      ],
      start,
      end,
    );
    expect(bands).toEqual([
      { x1: start, x2: toTimestamp("2026-02-01") },
      { x1: toTimestamp("2026-09-01"), x2: end },
    ]);
  });
});
