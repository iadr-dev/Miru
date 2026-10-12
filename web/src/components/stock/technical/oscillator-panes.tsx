"use client";

import { createChart, HistogramSeries, LineSeries, type Time } from "lightweight-charts";
import { type ReactNode, useEffect, useRef } from "react";
import { resolveToken } from "@/lib/resolve-css-token";
import type { TechnicalIndicatorsResponse } from "@/types/stock-detail";

export function KdPane({
  indicators,
}: {
  readonly indicators: TechnicalIndicatorsResponse;
}): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = containerRef.current;
    const kdResult = indicators.kd;
    if (!container || !kdResult) return;
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
    const dates = indicators.trade_dates;
    for (const [key, color] of [
      ["k", "var(--chart-1)"],
      ["d", "var(--chart-2)"],
    ] as const) {
      const line = chart.addSeries(LineSeries, {
        color: resolveToken(color),
        lineWidth: 1,
        priceLineVisible: false,
      });
      const points = dates
        .map((date, i) => ({ time: date as Time, value: kdResult[key][i] }))
        .filter(
          (p): p is { time: Time; value: number } => p.value !== null && p.value !== undefined,
        );
      line.setData(points);
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [indicators]);
  return <div ref={containerRef} className="h-32 w-full" />;
}

export function MacdPane({
  indicators,
}: {
  readonly indicators: TechnicalIndicatorsResponse;
}): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = containerRef.current;
    const macdResult = indicators.macd;
    if (!container || !macdResult) return;
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
    const dates = indicators.trade_dates;
    const histogram = chart.addSeries(HistogramSeries, { priceLineVisible: false });
    histogram.setData(
      dates
        .map((date, i) => {
          const value = macdResult.histogram[i];
          if (value === null || value === undefined) return null;
          return {
            time: date as Time,
            value,
            color: value >= 0 ? resolveToken("var(--chart-1)") : resolveToken("var(--chart-4)"),
          };
        })
        .filter((p): p is { time: Time; value: number; color: string } => p !== null),
    );
    for (const [key, color] of [
      ["dif", "var(--chart-2)"],
      ["dem", "var(--chart-3)"],
    ] as const) {
      const line = chart.addSeries(LineSeries, {
        color: resolveToken(color),
        lineWidth: 1,
        priceLineVisible: false,
      });
      const points = dates
        .map((date, i) => ({ time: date as Time, value: macdResult[key][i] }))
        .filter(
          (p): p is { time: Time; value: number } => p.value !== null && p.value !== undefined,
        );
      line.setData(points);
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [indicators]);
  return <div ref={containerRef} className="h-32 w-full" />;
}
