/** Multi-bar chart patterns (double top/bottom, head and shoulders, triangles, rectangle) for the
 * candlestick chart — pure helpers (no React, no chart library).
 *
 * The backend (`analytics/chart_patterns.py`) detects the shapes and returns their pivots and the
 * lines to draw; this module only decides which hits are drawn, how, and how a hit is summarised in
 * the list under the chart. Everything here is descriptive geometry: a status says where closes sit
 * relative to a line, never a direction to act in. */

import type {
  ChartPatternFamily,
  ChartPatternHit,
  ChartPatternPivot,
  ChartPatternStatus,
} from "@/types/stock/technical";

/** At most this many of the most recent unresolved-or-confirmed hits are drawn at once; a long
 * window can hold a dozen overlapping shapes and drawing all of them is unreadable. The list under
 * the chart always shows every hit. */
export const CHART_PATTERN_DRAW_LIMIT = 6;

/** Categorical `--chart-N` per family — never the signed up/down tokens. */
export const CHART_PATTERN_FAMILY_TOKEN: Record<ChartPatternFamily, string> = {
  reversal_top: "var(--chart-5)",
  reversal_bottom: "var(--chart-4)",
  consolidation: "var(--chart-2)",
};

const FAILED_TOKEN = "var(--text-muted)";

/** lightweight-charts `LineStyle` values: 0 solid, 1 dotted, 2 dashed. */
export type PatternLineStyle = 0 | 1 | 2;

export const CHART_PATTERN_STATUS_LINE_STYLE: Record<ChartPatternStatus, PatternLineStyle> = {
  confirmed: 0,
  forming: 2,
  failed: 1,
};

export interface PatternStroke {
  readonly token: string;
  readonly lineStyle: PatternLineStyle;
}

/** Colour by family, dash by status (solid confirmed, dashed forming, dotted + muted failed), so
 * status never relies on colour alone. */
export function patternStroke(hit: Pick<ChartPatternHit, "family" | "status">): PatternStroke {
  return {
    token: hit.status === "failed" ? FAILED_TOKEN : CHART_PATTERN_FAMILY_TOKEN[hit.family],
    lineStyle: CHART_PATTERN_STATUS_LINE_STYLE[hit.status],
  };
}

export function patternKey(
  hit: Pick<ChartPatternHit, "pattern" | "window_start" | "window_end">,
): string {
  return `${hit.pattern}|${hit.window_start.trade_date}|${hit.window_end.trade_date}`;
}

/** Newest structure first (by the date its last pivot formed). */
export function sortPatternsNewestFirst(hits: readonly ChartPatternHit[]): ChartPatternHit[] {
  return [...hits].sort(
    (a, b) =>
      b.window_end.trade_date.localeCompare(a.window_end.trade_date) ||
      b.window_start.trade_date.localeCompare(a.window_start.trade_date) ||
      a.pattern.localeCompare(b.pattern),
  );
}

/** Which hits go on the chart: with the toggle on, the most recent forming/confirmed ones (failed
 * shapes stay in the list only); the hit picked in the list is always drawn, toggle or not. */
export function patternsToDraw(
  hits: readonly ChartPatternHit[],
  showAll: boolean,
  selectedKey: string | null,
): ChartPatternHit[] {
  const picked = showAll
    ? sortPatternsNewestFirst(hits)
        .filter((h) => h.status !== "failed")
        .slice(0, CHART_PATTERN_DRAW_LIMIT)
    : [];
  const selected =
    selectedKey === null ? undefined : hits.find((h) => patternKey(h) === selectedKey);
  if (selected && !picked.some((h) => patternKey(h) === selectedKey)) picked.push(selected);
  return picked;
}

/** The first displayed bar on or after `date` (for a 週/月 series each bar is stamped with its
 * bucket's last day, so that is the bar containing the date); past the last bar it clamps to the
 * last one. `undefined` only when there are no bars. */
export function snapToBarDate(date: string, barDates: readonly string[]): string | undefined {
  return barDates.find((d) => d >= date) ?? barDates.at(-1);
}

export interface DrawnPatternLine {
  readonly key: string;
  readonly token: string;
  readonly lineStyle: PatternLineStyle;
  readonly lineWidth: 1 | 2 | 3;
  readonly points: readonly [
    { readonly time: string; readonly value: number },
    { readonly time: string; readonly value: number },
  ];
}

/** The hit's neckline / boundaries as two-point series on the displayed bar dates. A line whose
 * ends snap onto the same bar has no length on this timeframe and is skipped. The neckline is
 * heavier than the trendlines; the highlighted hit is heavier still. */
export function buildPatternLines(
  hit: ChartPatternHit,
  barDates: readonly string[],
  highlighted: boolean,
): DrawnPatternLine[] {
  const stroke = patternStroke(hit);
  const key = patternKey(hit);
  const out: DrawnPatternLine[] = [];
  for (const line of hit.lines) {
    const from = snapToBarDate(line.start.trade_date, barDates);
    const to = snapToBarDate(line.end.trade_date, barDates);
    if (from === undefined || to === undefined || from >= to) continue;
    const baseWidth = line.name === "neckline" ? 2 : 1;
    out.push({
      key: `${key}|${line.name}`,
      token: stroke.token,
      lineStyle: stroke.lineStyle,
      lineWidth: highlighted ? 3 : baseWidth,
      points: [
        { time: from, value: line.start.price },
        { time: to, value: line.end.price },
      ],
    });
  }
  return out;
}

export interface PatternPivotMarker {
  readonly time: string;
  readonly position: "aboveBar" | "belowBar";
  readonly token: string;
  /** Only the first pivot of a hit is labelled, so the chart is not a wall of text. */
  readonly labelled: boolean;
}

export interface MarkerBar {
  readonly trade_date: string;
  readonly high: number;
  readonly low: number;
}

/** One small marker per pivot, above the bar when the pivot sits in the upper half of it, below
 * otherwise — a pivot's price is that bar's high or low, so this follows the shape's own geometry
 * (peaks above, troughs below) without a separate role table. */
export function buildPatternPivotMarkers(
  hit: ChartPatternHit,
  bars: readonly MarkerBar[],
): PatternPivotMarker[] {
  const stroke = patternStroke(hit);
  const barDates = bars.map((b) => b.trade_date);
  const out: PatternPivotMarker[] = [];
  hit.pivots.forEach((pivot: ChartPatternPivot, i) => {
    const time = snapToBarDate(pivot.trade_date, barDates);
    const bar = time === undefined ? undefined : bars.find((b) => b.trade_date === time);
    if (time === undefined || !bar) return;
    out.push({
      time,
      position: pivot.price >= (bar.high + bar.low) / 2 ? "aboveBar" : "belowBar",
      token: stroke.token,
      labelled: i === 0,
    });
  });
  return out;
}

export interface PatternLevels {
  readonly neckline: number | null;
  readonly upper: number | null;
  readonly lower: number | null;
  readonly height: number;
}

function lineEnd(hit: ChartPatternHit, name: "neckline" | "upper" | "lower"): number | null {
  return hit.lines.find((l) => l.name === name)?.end.price ?? null;
}

/** The levels shown in the list: the neckline (reversal shapes, where a close is judged) or the
 * upper and lower boundary (consolidations), each as of the last bar the line is drawn to. */
export function patternLevels(hit: ChartPatternHit): PatternLevels {
  return {
    neckline: hit.breakout_level ?? lineEnd(hit, "neckline"),
    upper: lineEnd(hit, "upper"),
    lower: lineEnd(hit, "lower"),
    height: hit.height,
  };
}
