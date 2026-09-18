"use client";

import { useQuery } from "@tanstack/react-query";
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
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { ExpandModal } from "@/components/ui/expand-modal";
import {
  getForeignShareholding,
  getSecuritiesLending,
  getShareholderDispersion,
  getStockInstitutionalFlow,
  getStockMarginShort,
  getTechnicalIndicators,
} from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { resolveToken } from "@/lib/resolve-css-token";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";
import type { EodBar, TechnicalIndicatorsResponse } from "@/types/stock-detail";

type Timeframe = "day" | "week" | "month";
type RangeKey = "1m" | "3m" | "6m" | "ytd" | "1y" | "5y";
const RANGE_KEYS: readonly RangeKey[] = ["1m", "3m", "6m", "ytd", "1y", "5y"];

const MA_WINDOWS = [5, 10, 20, 60, 120, 240] as const;
type MaWindow = (typeof MA_WINDOWS)[number];
// One `--chart-N` per MA window, matching the design spec's "MA240 uses the 6th, --chart-6" rule.
const MA_COLOR: Record<MaWindow, string> = {
  5: "var(--chart-1)",
  10: "var(--chart-2)",
  20: "var(--chart-3)",
  60: "var(--chart-4)",
  120: "var(--chart-5)",
  240: "var(--chart-6)",
};

type SubOverlay =
  | "bollinger"
  | "kd"
  | "macd"
  | "institutional"
  | "bigHolders"
  | "foreignHolding"
  | "marginShort"
  | "securitiesLending"
  | "retail";

// A multi-year daily window can produce dozens of swing pivots / completed patterns — capping
// keeps the chart legible (see the call sites below for why).
const SR_LEVEL_LIMIT = 10;
const PATTERN_MARKER_LIMIT = 8;

/** The one `(symbol, market, startDate, endDate)` prop shape every date-ranged 副圖 data pane
 * below shares — declared once so it isn't retyped identically at every pane's own props type. */
interface RangedPaneProps {
  readonly symbol: string;
  readonly market: Market;
  readonly startDate: string;
  readonly endDate: string;
}

function rangeStartDate(range: RangeKey, today: Date): string {
  const d = new Date(today);
  switch (range) {
    case "1m":
      d.setMonth(d.getMonth() - 1);
      break;
    case "3m":
      d.setMonth(d.getMonth() - 3);
      break;
    case "6m":
      d.setMonth(d.getMonth() - 6);
      break;
    case "ytd":
      return `${today.getFullYear()}-01-01`;
    case "1y":
      d.setFullYear(d.getFullYear() - 1);
      break;
    case "5y":
      d.setFullYear(d.getFullYear() - 5);
      break;
    default:
      break;
  }
  return d.toISOString().slice(0, 10);
}

export function TechnicalAnalysisPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.technical");
  const [timeframe, setTimeframe] = useState<Timeframe>("day");
  const [range, setRange] = useState<RangeKey>("1y");
  const [visibleMa, setVisibleMa] = useState<ReadonlySet<MaWindow>>(new Set([5, 10, 20]));
  const [subOverlays, setSubOverlays] = useState<ReadonlySet<SubOverlay>>(new Set(["bollinger"]));
  const [showSupportResistance, setShowSupportResistance] = useState(true);
  const [showPatterns, setShowPatterns] = useState(true);
  const [expanded, setExpanded] = useState(false);

  const today = useMemo(() => new Date(), []);
  const startDate = useMemo(() => rangeStartDate(range, today), [range, today]);
  const endDate = useMemo(() => today.toISOString().slice(0, 10), [today]);

  const query = useQuery({
    queryKey: ["technical_indicators", symbol, market, startDate, endDate],
    queryFn: () => getTechnicalIndicators(symbol, market, { startDate, endDate }),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={480} />
      </section>
    );
  }
  const data = query.data;
  if (!data?.data_available || data.bars.length === 0) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      </section>
    );
  }

  // `timeframe` (日/週/月) only re-buckets the SAME fetched daily bars client-side (see
  // `bucketBars` below) — no separate weekly/monthly fetch, since only a daily
  // `Capability.PRICE_EOD` window is available at every range without a Sponsor gate.
  const bucketed = bucketBars(data.bars, timeframe);

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <div className="flex flex-wrap items-center gap-3">
          <TimeframeToggle value={timeframe} onChange={setTimeframe} t={t} />
          <RangeToggle value={range} onChange={setRange} t={t} />
        </div>
      </div>

      <MaToggleRow visible={visibleMa} onToggle={setVisibleMa} t={t} />
      <StructureToggleRow
        showSupportResistance={showSupportResistance}
        onToggleSupportResistance={setShowSupportResistance}
        showPatterns={showPatterns}
        onTogglePatterns={setShowPatterns}
        t={t}
      />
      <OverlayToggleRow value={subOverlays} onChange={setSubOverlays} t={t} />

      <KLineChart
        bars={bucketed}
        indicators={data}
        visibleMa={visibleMa}
        subOverlays={subOverlays}
        showSupportResistance={showSupportResistance}
        showPatterns={showPatterns}
        symbol={symbol}
        market={market}
        startDate={startDate}
        endDate={endDate}
        t={t}
      />

      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="mt-3 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
      >
        {t("datasheetTitle")}
      </button>
      <ExpandModal
        open={expanded}
        title={t("datasheetTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <Datasheet bars={data.bars} t={t} />
      </ExpandModal>

      <p className="mt-3 text-[11px] text-[var(--text-muted)]">{data.non_trading_caveat}</p>
    </section>
  );
}

function TimeframeToggle({
  value,
  onChange,
  t,
}: {
  readonly value: Timeframe;
  readonly onChange: (v: Timeframe) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const options: readonly Timeframe[] = ["day", "week", "month"];
  return (
    <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          aria-pressed={value === opt}
          className={cn(
            "rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px] transition-colors",
            value === opt
              ? "bg-[var(--accent)] text-[color:#06231F]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t(`timeframe.${opt}`)}
        </button>
      ))}
    </div>
  );
}

function RangeToggle({
  value,
  onChange,
  t,
}: {
  readonly value: RangeKey;
  readonly onChange: (v: RangeKey) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
      {RANGE_KEYS.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          aria-pressed={value === opt}
          className={cn(
            "rounded-[var(--radius-pill)] px-2 py-1 font-medium text-[11px] transition-colors",
            value === opt
              ? "bg-[var(--accent)] text-[color:#06231F]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t(`range.${opt}`)}
        </button>
      ))}
    </div>
  );
}

function MaToggleRow({
  visible,
  onToggle,
  t,
}: {
  readonly visible: ReadonlySet<MaWindow>;
  readonly onToggle: (next: ReadonlySet<MaWindow>) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <span className="text-[11px] text-[var(--text-muted)]">{t("maLabel")}</span>
      {MA_WINDOWS.map((window) => {
        const active = visible.has(window);
        return (
          <button
            key={window}
            type="button"
            onClick={() => {
              const next = new Set(visible);
              if (active) next.delete(window);
              else next.add(window);
              onToggle(next);
            }}
            aria-pressed={active}
            className="flex items-center gap-1 rounded-[var(--radius-pill)] px-2 py-1 text-[11px] transition-opacity"
            style={{
              backgroundColor: active
                ? `color-mix(in srgb, ${MA_COLOR[window]} 16%, transparent)`
                : "var(--surface-2)",
              color: active ? MA_COLOR[window] : "var(--text-muted)",
              opacity: active ? 1 : 0.6,
            }}
          >
            MA{window}
          </button>
        );
      })}
    </div>
  );
}

function StructureToggleRow({
  showSupportResistance,
  onToggleSupportResistance,
  showPatterns,
  onTogglePatterns,
  t,
}: {
  readonly showSupportResistance: boolean;
  readonly onToggleSupportResistance: (v: boolean) => void;
  readonly showPatterns: boolean;
  readonly onTogglePatterns: (v: boolean) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <span className="text-[11px] text-[var(--text-muted)]">{t("structureLabel")}</span>
      <button
        type="button"
        onClick={() => onToggleSupportResistance(!showSupportResistance)}
        aria-pressed={showSupportResistance}
        className={cn(
          "rounded-[var(--radius-pill)] px-2.5 py-1 text-[11px] transition-colors",
          showSupportResistance
            ? "bg-[var(--accent)] text-[color:#06231F]"
            : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
        )}
      >
        {t("structureSupportResistance")}
      </button>
      <button
        type="button"
        onClick={() => onTogglePatterns(!showPatterns)}
        aria-pressed={showPatterns}
        className={cn(
          "rounded-[var(--radius-pill)] px-2.5 py-1 text-[11px] transition-colors",
          showPatterns
            ? "bg-[var(--accent)] text-[color:#06231F]"
            : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
        )}
      >
        {t("structurePatterns")}
      </button>
    </div>
  );
}

const SUB_OVERLAYS: readonly SubOverlay[] = [
  "bollinger",
  "kd",
  "macd",
  "institutional",
  "bigHolders",
  "foreignHolding",
  "marginShort",
  "securitiesLending",
  "retail",
];

function OverlayToggleRow({
  value,
  onChange,
  t,
}: {
  readonly value: ReadonlySet<SubOverlay>;
  readonly onChange: (v: ReadonlySet<SubOverlay>) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <span className="text-[11px] text-[var(--text-muted)]">{t("overlayLabel")}</span>
      {SUB_OVERLAYS.map((overlay) => {
        const active = value.has(overlay);
        return (
          <button
            key={overlay}
            type="button"
            onClick={() => {
              const next = new Set(value);
              if (active) next.delete(overlay);
              else next.add(overlay);
              onChange(next);
            }}
            aria-pressed={active}
            className={cn(
              "rounded-[var(--radius-pill)] px-2.5 py-1 text-[11px] transition-colors",
              active
                ? "bg-[var(--accent)] text-[color:#06231F]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(`overlay.${overlay}`)}
          </button>
        );
      })}
    </div>
  );
}

/** Buckets daily `EodBar`s into weekly/monthly OHLCV bars for the 週/月 timeframe toggle — a
 * client-side re-aggregation of the one daily fetch, since a Sponsor-gated weekly/monthly
 * dataset is a separate, gated capability this public panel doesn't have access to (see the
 * survey this build was scoped against). ISO week (Mon-start) for 週, calendar month for 月. */
function bucketBars(bars: readonly EodBar[], timeframe: Timeframe): EodBar[] {
  if (timeframe === "day") return [...bars];
  const groups = new Map<string, EodBar[]>();
  for (const bar of bars) {
    const date = new Date(`${bar.trade_date}T00:00:00Z`);
    const key = timeframe === "month" ? bar.trade_date.slice(0, 7) : isoWeekKey(date);
    const group = groups.get(key) ?? [];
    group.push(bar);
    groups.set(key, group);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, group]) => {
      const sorted = [...group].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
      const first = sorted[0];
      const last = sorted.at(-1);
      if (!first || !last) {
        throw new Error("bucketBars: empty group");
      }
      return {
        symbol: first.symbol,
        market: first.market,
        trade_date: last.trade_date,
        open: first.open,
        high: Math.max(...sorted.map((b) => b.high)),
        low: Math.min(...sorted.map((b) => b.low)),
        close: last.close,
        volume: sorted.reduce((sum, b) => sum + b.volume, 0),
      };
    });
}

function isoWeekKey(date: Date): string {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() - dayNum + 1);
  return d.toISOString().slice(0, 10);
}

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

function KLineChart({
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

function KdPane({ indicators }: { readonly indicators: TechnicalIndicatorsResponse }): ReactNode {
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

function MacdPane({ indicators }: { readonly indicators: TechnicalIndicatorsResponse }): ReactNode {
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

/** Shared shell every line-chart 副圖 pane below uses — same layout/grid/axis options as
 * `KdPane`/`MacdPane`, just parameterized over its own line series so the three data-fetching
 * panes (融資融券/借券賣出/散戶) don't each hand-roll the chart bootstrap. */
function useLineChartPane(
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

function MarginShortPane({ symbol, market, startDate, endDate }: RangedPaneProps): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["margin_short", symbol, market, startDate, endDate],
    queryFn: () => getStockMarginShort(symbol, market, { startDate, endDate }),
  });
  const rows = [...(query.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );

  useLineChartPane(
    containerRef,
    (chart) => {
      const marginLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-1)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      marginLine.setData(
        rows.map((r) => ({ time: r.trade_date as Time, value: r.margin_purchase_today_balance })),
      );
      const shortLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-4)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      shortLine.setData(
        rows.map((r) => ({ time: r.trade_date as Time, value: r.short_sale_today_balance })),
      );
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

function SecuritiesLendingPane({ symbol, market, startDate, endDate }: RangedPaneProps): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["securities_lending", symbol, market, startDate, endDate],
    queryFn: () => getSecuritiesLending(symbol, market, { startDate, endDate }),
  });
  // `get_securities_lending` can carry more than one row per `trade_date` (distinct
  // `transaction_type`s) — lightweight-charts requires strictly-ascending, unique timestamps
  // per series, so same-day rows are summed into one point rather than plotted as duplicates.
  const rows = useMemo(() => {
    const byDate = new Map<string, number>();
    for (const row of query.data?.rows ?? []) {
      byDate.set(row.trade_date, (byDate.get(row.trade_date) ?? 0) + row.volume);
    }
    return [...byDate.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([trade_date, volume]) => ({ trade_date, volume }));
  }, [query.data]);

  useLineChartPane(
    containerRef,
    (chart) => {
      const volumeLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-2)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      volumeLine.setData(rows.map((r) => ({ time: r.trade_date as Time, value: r.volume })));
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

function RetailDispersionPane({
  symbol,
  market,
  startDate,
  endDate,
  t,
}: RangedPaneProps & { readonly t: ReturnType<typeof useTranslations> }): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["shareholder_dispersion", symbol, market, startDate, endDate],
    queryFn: () => getShareholderDispersion(symbol, market, { startDate, endDate }),
  });
  const data = query.data;
  const locked = data !== undefined && isMaskedTeaser(data);
  // 散戶 as a percent-of-custody share (小額 <1000 shares tiers) rather than raw people-count —
  // the count alone can't be plotted alongside a percent series on one axis, and percent is the
  // more directly comparable "散戶 concentration" read across dates.
  const rows =
    data !== undefined && !isMaskedTeaser(data)
      ? data.rows
          .filter(
            (r) =>
              r.holding_shares_level.includes("1-999") || r.holding_shares_level.includes("999"),
          )
          .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
      : [];

  useLineChartPane(
    containerRef,
    (chart) => {
      const percentLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-3)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      percentLine.setData(rows.map((r) => ({ time: r.trade_date as Time, value: r.percent })));
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (!data) return null;
  if (locked && isMaskedTeaser(data)) {
    return (
      <SponsorTeaser
        headline={t("overlay.retail")}
        preview={data.preview}
        requiredTier={data.required_tier}
      >
        <div className="h-32 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      </SponsorTeaser>
    );
  }
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

// 外資 (Foreign_Investor + Foreign_Dealer_Self) / 投信 (Investment_Trust) / 自營商 (Dealer_self +
// Dealer_Hedging) — the same 3-bucket collapse `chip-analysis-panel.tsx`'s own
// `BUCKET_BY_INVESTOR`/`bucketedFlowByDate` use, duplicated here rather than imported since
// that module doesn't export them and this is the one other place in the app plotting the same
// 三大法人 buckets.
type InstitutionalBucket = "foreign" | "trust" | "dealer";
const INSTITUTIONAL_BUCKET_BY_INVESTOR: Record<string, InstitutionalBucket> = {
  Foreign_Investor: "foreign",
  Foreign_Dealer_Self: "foreign",
  Investment_Trust: "trust",
  Dealer_self: "dealer",
  Dealer_Hedging: "dealer",
};
const INSTITUTIONAL_BUCKET_COLOR: Record<InstitutionalBucket, string> = {
  foreign: "var(--chart-1)",
  trust: "var(--chart-2)",
  dealer: "var(--chart-3)",
};

function InstitutionalFlowPane({
  symbol,
  market,
  startDate,
  endDate,
  t,
}: RangedPaneProps & { readonly t: ReturnType<typeof useTranslations> }): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market],
    queryFn: () => getStockInstitutionalFlow(symbol, market),
  });
  const rows = (query.data?.rows ?? []).filter(
    (r) => r.trade_date >= startDate && r.trade_date <= endDate,
  );

  // Net buy-sell per bucket per day, then a cumulative running total — the buy/sell delta alone
  // is too noisy day-to-day to read as a trend line; the reference design's 三大法人 line reads
  // as an accumulated position, not a daily net.
  const byDate = useMemo(() => {
    const dayTotals = new Map<string, Record<InstitutionalBucket, number>>();
    for (const row of rows) {
      const bucket = INSTITUTIONAL_BUCKET_BY_INVESTOR[row.investor];
      if (!bucket) continue;
      const entry = dayTotals.get(row.trade_date) ?? { foreign: 0, trust: 0, dealer: 0 };
      entry[bucket] += row.buy - row.sell;
      dayTotals.set(row.trade_date, entry);
    }
    const dates = [...dayTotals.keys()].sort((a, b) => a.localeCompare(b));
    let foreignCum = 0;
    let trustCum = 0;
    let dealerCum = 0;
    return dates.map((date) => {
      const entry = dayTotals.get(date);
      foreignCum += entry?.foreign ?? 0;
      trustCum += entry?.trust ?? 0;
      dealerCum += entry?.dealer ?? 0;
      return { date, foreign: foreignCum, trust: trustCum, dealer: dealerCum };
    });
  }, [rows]);

  useLineChartPane(
    containerRef,
    (chart) => {
      for (const bucket of ["foreign", "trust", "dealer"] as const) {
        const line = chart.addSeries(LineSeries, {
          color: resolveToken(INSTITUTIONAL_BUCKET_COLOR[bucket]),
          lineWidth: 1,
          priceLineVisible: false,
        });
        line.setData(byDate.map((p) => ({ time: p.date as Time, value: p[bucket] })));
      }
    },
    [byDate],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (byDate.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap gap-3">
        {(["foreign", "trust", "dealer"] as const).map((bucket) => (
          <span
            key={bucket}
            className="inline-flex items-center gap-1.5 text-[11px]"
            style={{ color: INSTITUTIONAL_BUCKET_COLOR[bucket] }}
          >
            <span
              aria-hidden
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ backgroundColor: INSTITUTIONAL_BUCKET_COLOR[bucket] }}
            />
            {t(`institutionalBucket.${bucket}`)}
          </span>
        ))}
      </div>
      <div ref={containerRef} className="h-32 w-full" />
    </div>
  );
}

function ForeignHoldingPane({ symbol, market, startDate, endDate }: RangedPaneProps): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["stock_foreign_shareholding", symbol, market, startDate, endDate],
    queryFn: () => getForeignShareholding(symbol, market, { startDate, endDate }),
  });
  const rows = [...(query.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );

  useLineChartPane(
    containerRef,
    (chart) => {
      const ratioLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-1)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      ratioLine.setData(
        rows.map((r) => ({ time: r.trade_date as Time, value: r.foreign_investment_shares_ratio })),
      );
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

function BigHoldersPane({
  symbol,
  market,
  startDate,
  endDate,
  t,
}: RangedPaneProps & { readonly t: ReturnType<typeof useTranslations> }): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["shareholder_dispersion_big_holders", symbol, market, startDate, endDate],
    queryFn: () => getShareholderDispersion(symbol, market, { startDate, endDate }),
  });
  const data = query.data;
  const locked = data !== undefined && isMaskedTeaser(data);
  // 大戶持股 as the cumulative %-of-custody share held by every tier from 400張 up (matches
  // `get_holder_accumulation`'s own TIER_400 threshold convention — the "1,000-share-lot big
  // holder" figure this reference design's 大戶持股 checkbox names).
  const rows =
    data !== undefined && !isMaskedTeaser(data)
      ? data.rows
          .filter((r) => {
            const lower = Number.parseInt(
              r.holding_shares_level.split("-")[0]?.replaceAll(",", "") ?? "",
              10,
            );
            return Number.isFinite(lower) && lower >= 400_001;
          })
          .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
      : [];
  const byDate = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of rows) {
      totals.set(row.trade_date, (totals.get(row.trade_date) ?? 0) + row.percent);
    }
    return [...totals.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, percent]) => ({ date, percent }));
  }, [rows]);

  useLineChartPane(
    containerRef,
    (chart) => {
      const percentLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-5)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      percentLine.setData(byDate.map((p) => ({ time: p.date as Time, value: p.percent })));
    },
    [byDate],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (!data) return null;
  if (locked && isMaskedTeaser(data)) {
    return (
      <SponsorTeaser
        headline={t("overlay.bigHolders")}
        preview={data.preview}
        requiredTier={data.required_tier}
      >
        <div className="h-32 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      </SponsorTeaser>
    );
  }
  if (byDate.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

function formatChangePercent(changePct: number, up: boolean, down: boolean): string {
  let arrow = "";
  if (up) arrow = "▲";
  else if (down) arrow = "▼";
  const sign = changePct >= 0 ? "+" : "";
  return `${arrow}${sign}${changePct.toFixed(2)}%`;
}

function Datasheet({
  bars,
  t,
}: {
  readonly bars: readonly EodBar[];
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();
  const ordered = [...bars].sort((a, b) => b.trade_date.localeCompare(a.trade_date));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead className="sticky top-0 bg-[var(--surface)]">
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("datasheet.date")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("datasheet.open")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("datasheet.high")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("datasheet.low")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("datasheet.close")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("datasheet.changePercent")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("datasheet.volume")}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {ordered.map((bar, i) => {
            const prev = ordered[i + 1];
            const changePct =
              prev && prev.close !== 0 ? ((bar.close - prev.close) / prev.close) * 100 : null;
            const up = changePct !== null && changePct > 0;
            const down = changePct !== null && changePct < 0;
            return (
              <tr key={bar.trade_date}>
                <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">
                  {bar.trade_date}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(bar.open, locale, { decimals: 2 })}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(bar.high, locale, { decimals: 2 })}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(bar.low, locale, { decimals: 2 })}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(bar.close, locale, { decimals: 2 })}
                </td>
                <td
                  className={cn(
                    "px-2 py-1.5 text-right font-num tabular-nums",
                    up && "text-[var(--up)]",
                    down && "text-[var(--down)]",
                    !up && !down && "text-[var(--text-muted)]",
                  )}
                >
                  {changePct === null ? "—" : formatChangePercent(changePct, up, down)}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(Math.round(bar.volume / 1000), locale)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
