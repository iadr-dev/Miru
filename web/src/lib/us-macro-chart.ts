/**
 * Chart helpers for the US macro section: categorical colours per group (never the signed
 * price-move tokens), range windows and the recession-band clamp.
 */

import { parseIsoDate } from "@/lib/us-macro-format";
import type { MacroGroup, MacroPoint, RecessionBand } from "@/types/market/us-macro";

export const GROUP_CHART_COLOR: Record<MacroGroup, string> = {
  yield_curve_recession: "var(--chart-1)",
  inflation: "var(--chart-3)",
  labor: "var(--chart-4)",
  credit_financial_conditions: "var(--chart-2)",
  policy_liquidity: "var(--chart-1)",
  growth: "var(--chart-5)",
  housing: "var(--chart-6)",
  dollar: "var(--chart-2)",
  money: "var(--chart-4)",
};

export const RANGE_YEARS = { "1Y": 1, "3Y": 3, "5Y": 5, "10Y": 10 } as const;

export type RangeKey = keyof typeof RANGE_YEARS;

export const RANGE_KEYS = ["1Y", "3Y", "5Y", "10Y"] as const satisfies readonly RangeKey[];

/** ISO date `years` before `from` (UTC). */
export function yearsBefore(from: Date, years: number): string {
  const date = new Date(from);
  date.setUTCFullYear(date.getUTCFullYear() - years);
  return date.toISOString().slice(0, 10);
}

export function toTimestamp(iso: string): number {
  return parseIsoDate(iso)?.getTime() ?? Number.NaN;
}

/** Points on or after the window that ends at the last point: "3Y" shows the three years up to
 * the newest observation, so it does not depend on the viewer's clock. */
export function sliceToRange(points: readonly MacroPoint[], years: number): MacroPoint[] {
  const last = points.at(-1);
  if (!last) {
    return [];
  }
  const lastDate = parseIsoDate(last.date);
  if (lastDate === null) {
    return [...points];
  }
  const cutoff = yearsBefore(lastDate, years);
  return points.filter((point) => point.date >= cutoff);
}

export type TimeBand = { readonly x1: number; readonly x2: number };

/** Recession bands clipped to the visible time window. An ongoing band runs to the last point;
 * a band that falls outside the window is dropped. */
export function clampRecessionBands(
  bands: readonly RecessionBand[],
  windowStart: number,
  windowEnd: number,
): TimeBand[] {
  const result: TimeBand[] = [];
  for (const band of bands) {
    const start = toTimestamp(band.start);
    const end = band.ongoing ? windowEnd : toTimestamp(band.end);
    if (Number.isNaN(start) || Number.isNaN(end)) {
      continue;
    }
    const x1 = Math.max(start, windowStart);
    const x2 = Math.min(end, windowEnd);
    if (x2 > x1) {
      result.push({ x1, x2 });
    }
  }
  return result;
}
