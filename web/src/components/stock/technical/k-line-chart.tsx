"use client";

import {
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  LineSeries,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useMemo, useRef } from "react";
import {
  MarginShortPane,
  RetailDispersionPane,
  SecuritiesLendingPane,
} from "@/components/stock/technical/chip-panes";
import {
  BigHoldersPane,
  ForeignHoldingPane,
  InstitutionalFlowPane,
} from "@/components/stock/technical/holder-panes";
import { KdPane, MacdPane } from "@/components/stock/technical/oscillator-panes";
import {
  MA_COLOR,
  MA_WINDOWS,
  type MaWindow,
  type SubOverlay,
} from "@/components/stock/technical/shared";
import { resolveToken } from "@/lib/resolve-css-token";
import type { Market } from "@/types/settings";
import type { EodBar, TechnicalIndicatorsResponse } from "@/types/stock-detail";

// A multi-year daily window can produce dozens of swing pivots / completed patterns — capping
// keeps the chart legible (see the call sites below for why).
const SR_LEVEL_LIMIT = 10;

const PATTERN_MARKER_LIMIT = 8;

/** A bearish pattern pins ABOVE the bar it completed on, bullish BELOW, neutral (doji) ON the
 * bar — position (not color) carries the direction, per the non-trading governance rule. */
function patternMarkerPosition(direction: number): "aboveBar" | "belowBar" | "inBar" {
  if (direction === -1) return "aboveBar";
  if (direction === 1) return "belowBar";
  return "inBar";
}

function drawMovingAverages(
  chart: IChartApi,
  visibleMa: ReadonlySet<MaWindow>,
  maSeriesByWindow: Record<MaWindow, { time: Time; value: number }[]>,
): void {
  for (const window of MA_WINDOWS) {
    if (!visibleMa.has(window)) continue;
    const points = maSeriesByWindow[window];
    if (!points || points.length === 0) continue;
    const line = chart.addSeries(LineSeries, {
      color: resolveToken(MA_COLOR[window]),
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    line.setData(points);
  }
}

/** 支撐/壓力 as dashed muted h-lines with a label — never colored as signed price. Capped to the
 * most recent pivots: a multi-year daily window can carry dozens of swing pivots, and drawing
 * every one turns the chart into a stack of overlapping h-lines nobody can read — only the most
 * recent (most currently-relevant) handful renders. */
function drawSupportResistance(
  candleSeries: ISeriesApi<"Candlestick">,
  indicators: TechnicalIndicatorsResponse,
  t: ReturnType<typeof useTranslations>,
): void {
  const srLevels = (indicators.support_resistance?.levels ?? []).slice(-SR_LEVEL_LIMIT);
  for (const level of srLevels) {
    candleSeries.createPriceLine({
      price: level.price,
      color: resolveToken("var(--text-muted)"),
      lineWidth: 1,
      lineStyle: 2, // Dashed
      axisLabelVisible: true,
      title: level.kind === "support" ? t("supportLabel") : t("resistanceLabel"),
    });
  }
}

/** K線型態 hits as small accent-hairline pins — never colored by direction alone (position
 * above/below the bar carries +1看多/-1看空 instead, per the non-trading governance rule). One
 * marker per bar (a bar completing several patterns only shows the first, avoiding stacked
 * same-timestamp markers) and capped to the most recent few — a long daily window completes
 * dozens of short-lived patterns, and drawing every one is unreadable. */
function drawPatternMarkers(
  candleSeries: ISeriesApi<"Candlestick">,
  indicators: TechnicalIndicatorsResponse,
  t: ReturnType<typeof useTranslations>,
): void {
  const hitsByBar = new Map<number, (typeof indicators.candlestick_patterns)[number]>();
  for (const hit of indicators.candlestick_patterns) {
    if (!hitsByBar.has(hit.bar_index)) hitsByBar.set(hit.bar_index, hit);
  }
  const hits = [...hitsByBar.values()]
    .sort((a, b) => a.bar_index - b.bar_index)
    .slice(-PATTERN_MARKER_LIMIT);
  if (hits.length === 0) return;

  const markers: SeriesMarker<Time>[] = [];
  for (const hit of hits) {
    const date = indicators.trade_dates[hit.bar_index];
    if (!date) continue;
    markers.push({
      time: date as Time,
      position: patternMarkerPosition(hit.direction),
      color: resolveToken("var(--accent)"),
      shape: "circle",
      text: t(`pattern.${hit.pattern}` as `pattern.${string}`),
    });
  }
  createSeriesMarkers(candleSeries, markers);
}

/** 布林通道 (Bollinger Bands) draws directly on the MAIN candle pane, not a separate subpane —
 * it's a price-scale overlay (upper/mid/lower bands sit on the same axis as the candles
 * themselves), unlike KD/MACD which are their own oscillator scale. */
function drawBollinger(chart: IChartApi, indicators: TechnicalIndicatorsResponse): void {
  const boll = indicators.bollinger;
  if (!boll) return;
  const dates = indicators.trade_dates;
  for (const [key, color] of [
    ["upper", "var(--chart-3)"],
    ["mid", "var(--chart-2)"],
    ["lower", "var(--chart-4)"],
  ] as const) {
    const line = chart.addSeries(LineSeries, {
      color: resolveToken(color),
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    const points = dates
      .map((date, i) => ({ time: date as Time, value: boll[key][i] }))
      .filter((p): p is { time: Time; value: number } => p.value !== null && p.value !== undefined);
    line.setData(points);
  }
}

/** Volume renders in its OWN pane (paneIndex 1) below the price pane — sharing this chart's time
 * axis/crosshair rather than being squeezed onto the price pane's own price-scale via
 * `scaleMargins` (that approach visually collapsed the bars to a near-invisible strip at the
 * very bottom of the candle pane). Matches the reference design's separate volume band. */
function drawVolumePane(chart: IChartApi, bars: readonly EodBar[]): void {
  const volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: "volume" } }, 1);
  volumeSeries.setData(
    bars.map((b) => ({
      time: b.trade_date as Time,
      value: b.volume,
      color: b.close < b.open ? resolveToken("var(--down)") : resolveToken("var(--up)"),
    })),
  );
  chart.panes()[0]?.setStretchFactor(3);
  chart.panes()[1]?.setStretchFactor(1);
}

export function KLineChart({
  bars,
  indicators,
  visibleMa,
  subOverlays,
  showSupportResistance,
  showPatterns,
  symbol,
  market,
  startDate,
  endDate,
  t,
}: {
  readonly bars: readonly EodBar[];
  readonly indicators: TechnicalIndicatorsResponse;
  readonly visibleMa: ReadonlySet<MaWindow>;
  readonly subOverlays: ReadonlySet<SubOverlay>;
  readonly showSupportResistance: boolean;
  readonly showPatterns: boolean;
  readonly symbol: string;
  readonly market: Market;
  readonly startDate: string;
  readonly endDate: string;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  // MA/support-resistance/pattern markers are computed from the RAW daily bar series
  // (`indicators`'s own trade_dates) — they never re-align onto a client-bucketed 週/月 series,
  // since the backend's MA/KD/MACD/pattern math is itself always daily-bar-based (per
  // `get_technical_indicators`'s own contract). The 週/月 toggle only changes the candle/volume
  // display granularity, not which indicator values exist.
  const maSeriesByWindow = useMemo(() => {
    const bundle = indicators.moving_averages;
    if (!bundle) return {} as Record<MaWindow, { time: Time; value: number }[]>;
    const dates = indicators.trade_dates;
    const out: Record<MaWindow, { time: Time; value: number }[]> = {
      5: [],
      10: [],
      20: [],
      60: [],
      120: [],
      240: [],
    };
    for (const window of MA_WINDOWS) {
      const key = `ma${window}` as const;
      const series = bundle[key];
      out[window] = dates
        .map((date, i) => ({ time: date as Time, value: series.values[i] }))
        .filter(
          (p): p is { time: Time; value: number } => p.value !== null && p.value !== undefined,
        );
    }
    return out;
  }, [indicators]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || bars.length === 0) return;

    const chart = createChart(container, {
      layout: {
        background: { color: "transparent" },
        textColor: resolveToken("var(--text-muted)"),
      },
      grid: {
        horzLines: { color: resolveToken("var(--grid)") },
        vertLines: { color: resolveToken("var(--grid)") },
      },
      timeScale: { borderColor: resolveToken("var(--border)") },
      rightPriceScale: { borderColor: resolveToken("var(--border)") },
      crosshair: {
        vertLine: { color: resolveToken("var(--crosshair)") },
        horzLine: { color: resolveToken("var(--crosshair)") },
      },
      autoSize: true,
    });
    chartRef.current = chart;

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: resolveToken("var(--up)"),
      downColor: resolveToken("var(--down)"),
      borderVisible: false,
      wickUpColor: resolveToken("var(--up)"),
      wickDownColor: resolveToken("var(--down)"),
    });
    candleSeries.priceScale().applyOptions({ scaleMargins: { top: 0.05, bottom: 0.05 } });
    candleSeries.setData(
      bars.map((b) => ({
        time: b.trade_date as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );

    drawMovingAverages(chart, visibleMa, maSeriesByWindow);
    if (showSupportResistance) drawSupportResistance(candleSeries, indicators, t);
    if (showPatterns) drawPatternMarkers(candleSeries, indicators, t);
    if (subOverlays.has("bollinger") && indicators.bollinger) drawBollinger(chart, indicators);
    drawVolumePane(chart, bars);

    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [
    bars,
    indicators,
    visibleMa,
    maSeriesByWindow,
    showSupportResistance,
    showPatterns,
    subOverlays,
    t,
  ]);

  return (
    <div className="flex flex-col gap-3">
      <div ref={containerRef} className="h-[480px] w-full" />
      {subOverlays.has("kd") ? <KdPane indicators={indicators} /> : null}
      {subOverlays.has("macd") ? <MacdPane indicators={indicators} /> : null}
      {subOverlays.has("institutional") ? (
        <InstitutionalFlowPane
          symbol={symbol}
          market={market}
          startDate={startDate}
          endDate={endDate}
          t={t}
        />
      ) : null}
      {subOverlays.has("bigHolders") ? (
        <BigHoldersPane
          symbol={symbol}
          market={market}
          startDate={startDate}
          endDate={endDate}
          t={t}
        />
      ) : null}
      {subOverlays.has("foreignHolding") ? (
        <ForeignHoldingPane
          symbol={symbol}
          market={market}
          startDate={startDate}
          endDate={endDate}
        />
      ) : null}
      {subOverlays.has("marginShort") ? (
        <MarginShortPane symbol={symbol} market={market} startDate={startDate} endDate={endDate} />
      ) : null}
      {subOverlays.has("securitiesLending") ? (
        <SecuritiesLendingPane
          symbol={symbol}
          market={market}
          startDate={startDate}
          endDate={endDate}
        />
      ) : null}
      {subOverlays.has("retail") ? (
        <RetailDispersionPane
          symbol={symbol}
          market={market}
          startDate={startDate}
          endDate={endDate}
          t={t}
        />
      ) : null}
    </div>
  );
}
