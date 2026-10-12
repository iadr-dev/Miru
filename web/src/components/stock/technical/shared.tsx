"use client";

import { createChart, type IChartApi } from "lightweight-charts";
import { type RefObject, useEffect } from "react";
import { resolveToken } from "@/lib/resolve-css-token";
import type { Market } from "@/types/settings";

export type Timeframe = "day" | "week" | "month";

export type RangeKey = "1m" | "3m" | "6m" | "ytd" | "1y" | "5y";

export const MA_WINDOWS = [5, 10, 20, 60, 120, 240] as const;

export type MaWindow = (typeof MA_WINDOWS)[number];

// One `--chart-N` per MA window, matching the design spec's "MA240 uses the 6th, --chart-6" rule.
export const MA_COLOR: Record<MaWindow, string> = {
  5: "var(--chart-1)",
  10: "var(--chart-2)",
  20: "var(--chart-3)",
  60: "var(--chart-4)",
  120: "var(--chart-5)",
  240: "var(--chart-6)",
};

export type SubOverlay =
  | "bollinger"
  | "kd"
  | "macd"
  | "institutional"
  | "bigHolders"
  | "foreignHolding"
  | "marginShort"
  | "securitiesLending"
  | "retail";

/** The one `(symbol, market, startDate, endDate)` prop shape every date-ranged 副圖 data pane
 * below shares — declared once so it isn't retyped identically at every pane's own props type. */
export interface RangedPaneProps {
  readonly symbol: string;
  readonly market: Market;
  readonly startDate: string;
  readonly endDate: string;
}

/** Shared shell every line-chart 副圖 pane below uses — same layout/grid/axis options as
 * `KdPane`/`MacdPane`, just parameterized over its own line series so the three data-fetching
 * panes (融資融券/借券賣出/散戶) don't each hand-roll the chart bootstrap. */
export function useLineChartPane(
  containerRef: RefObject<HTMLDivElement | null>,
  buildSeries: (chart: IChartApi) => void,
  deps: readonly unknown[],
): void {
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const chart = createChart(container, {
      layout: {
        background: { color: "transparent" },
        textColor: resolveToken("var(--text-muted)"),
      },
      grid: {
        horzLines: { color: resolveToken("var(--grid)") },
        vertLines: { color: resolveToken("var(--grid)") },
      },
      timeScale: { borderColor: resolveToken("var(--border)"), visible: true },
      rightPriceScale: { borderColor: resolveToken("var(--border)") },
      autoSize: true,
    });
    buildSeries(chart);
    chart.timeScale().fitContent();
    return () => chart.remove();
    // biome-ignore lint/correctness/useExhaustiveDependencies: deps is the caller-supplied list
  }, deps);
}
