"use client";

import {
  type CandlestickData,
  CandlestickSeries,
  createChart,
  type HistogramData,
  HistogramSeries,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartWithDataSheet } from "@/components/ui/chart-with-data-sheet";
import { fieldLabel } from "@/lib/field-labels";
import { asArray, asNumber, formatCompactNumber, formatNumber, rowDate } from "@/lib/format-number";
import { resolveToken } from "@/lib/resolve-css-token";

/** Field-name aliases each OHLC corner accepts, checked in order — covers every price-bearing
 * card type's own naming, not just the plain `open`/`high`/`low`/`close` most EOD-bar rows use.
 * `dividend_result` (除權除息結果) names its corners `open_price`/`max_price`/`min_price` and has
 * no `close` at all — `reference_price` (the post-adjustment reference the exchange publishes for
 * that ex-dividend day) is its closing-equivalent. Any card whose rows carry a real stock/ETF
 * price series renders as a K-line, per this codebase's convention that price data is candlestick
 * data — never a plain line, which flattens the day's actual trading range into one point. */
const OPEN_ALIASES = ["open", "open_price"];
const HIGH_ALIASES = ["high", "max_price"];
const LOW_ALIASES = ["low", "min_price"];
const CLOSE_ALIASES = ["close", "reference_price"];

function firstNumeric(row: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = asNumber(row[key]);
    if (value !== null) {
      return value;
    }
  }
  return null;
}

/** A row is candlestick-eligible once it carries all four OHLC corners (under any of their known
 * aliases) — this is the one shape TradingView-style price/OHLCV charting is actually meant for
 * (per lightweight-charts' own scope: https://www.tradingview.com/x/xTW9HDQE/). Everything else in
 * the timeseries bucket (ratios, flows, ...) is multi-metric line data, not a price chart, and
 * renders via recharts below instead. */
function toCandles(rows: Record<string, unknown>[]): CandlestickData<Time>[] {
  const points: CandlestickData<Time>[] = [];
  for (const row of rows) {
    const time = rowDate(row);
    const open = firstNumeric(row, OPEN_ALIASES);
    const high = firstNumeric(row, HIGH_ALIASES);
    const low = firstNumeric(row, LOW_ALIASES);
    const close = firstNumeric(row, CLOSE_ALIASES);
    if (time && open !== null && high !== null && low !== null && close !== null) {
      points.push({ time: time as Time, open, high, low, close });
    }
  }
  return points;
}

/** `volume`/`trading_volume` aliases — covers `EODBar`/`KBarRow`'s `volume` and
 * `PeriodPriceRow`'s `trading_volume`. Absent on `dividend_result` (no volume field at all), so
 * the histogram pane is simply omitted there rather than showing an empty/zero series. */
const VOLUME_ALIASES = ["volume", "trading_volume"];

function toVolumes(rows: Record<string, unknown>[]): HistogramData<Time>[] {
  const points: HistogramData<Time>[] = [];
  for (const row of rows) {
    const time = rowDate(row);
    const volume = firstNumeric(row, VOLUME_ALIASES);
    const close = firstNumeric(row, CLOSE_ALIASES);
    const open = firstNumeric(row, OPEN_ALIASES);
    if (time && volume !== null) {
      points.push({
        time: time as Time,
        value: volume,
        // 紅漲綠跌: a volume bar tints with the same day's direction as its candle, never a
        // neutral gray — matches the reference TradingView layout's red/green volume histogram.
        color:
          close !== null && open !== null && close < open
            ? resolveToken("var(--down)")
            : resolveToken("var(--up)"),
      });
    }
  }
  return points;
}

/** The first numeric, non-OHLC fields across a row set become the plotted metric lines — covers
 * the many single/multi-metric series that don't share one fixed field name (`taiex`, `value`,
 * `close`, `per`, `pbr`, `dividend_yield`, `market_value`, ...), up to 6 lines. */
const NON_METRIC_KEYS = new Set([
  "trade_date",
  "date",
  "time",
  "period",
  "minute",
  "note",
  "symbol",
  "market",
]);

/** `revenue_month`/`revenue_year`, `fiscal_year`, etc. are calendar metadata that happens to
 * parse as a number, not a value worth plotting — excluded by suffix rather than by naming every
 * backend field, since the same pattern recurs across card_types. */
function isCalendarMetadataKey(key: string): boolean {
  return key.endsWith("_year") || key.endsWith("_month") || key.endsWith("_day");
}

function numericKeysIn(rows: Record<string, unknown>[]): string[] {
  const keys = new Set<string>();
  for (const row of rows) {
    for (const [key, value] of Object.entries(row)) {
      if (!NON_METRIC_KEYS.has(key) && !isCalendarMetadataKey(key) && asNumber(value) !== null) {
        keys.add(key);
      }
    }
  }
  return [...keys];
}

const LINE_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
];

/** Renders a candlestick-eligible timeseries card as a real price chart via `lightweight-charts`
 * (the one place in the chat surface that isn't plain React-rendered DOM, mirroring how the
 * project already scopes canvas-owning libraries per web/OVERVIEW.md's "lightweight-charts for
 * candles" call) — everything else in the timeseries bucket renders as a `recharts` multi-line
 * chart (`TimeseriesLineChart` below), since it isn't price/OHLCV data. */
export function TimeseriesChart({ data }: Readonly<{ data: unknown }>): ReactNode {
  const rows = extractRows(data);
  const candles = toCandles(rows);

  if (candles.length > 0) {
    return <CandlestickChart candles={candles} volumes={toVolumes(rows)} rows={rows} />;
  }
  return <TimeseriesLineChart rows={rows} />;
}

/** Volume renders as a histogram pane occupying the bottom ~20% of the same chart (via a
 * dedicated `overlay` price scale + `scaleMargins`, `lightweight-charts`' documented pattern for
 * a secondary indicator pane) rather than a separate chart — matches the reference TradingView
 * layout (price candles on top, red/green volume bars below, one shared time axis). Omitted
 * entirely when the row shape carries no volume field (e.g. `dividend_result`), never a
 * zero-filled placeholder pane. */
function CandlestickChart({
  candles,
  volumes,
  rows,
}: Readonly<{
  candles: CandlestickData<Time>[];
  volumes: HistogramData<Time>[];
  rows: Record<string, unknown>[];
}>): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
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

    const series = chart.addSeries(CandlestickSeries, {
      upColor: resolveToken("var(--up)"),
      downColor: resolveToken("var(--down)"),
      borderVisible: false,
      wickUpColor: resolveToken("var(--up)"),
      wickDownColor: resolveToken("var(--down)"),
    });
    series
      .priceScale()
      .applyOptions({ scaleMargins: { top: 0.05, bottom: volumes.length > 0 ? 0.3 : 0.05 } });
    series.setData(candles);

    if (volumes.length > 0) {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
      });
      volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volumeSeries.setData(volumes);
    }

    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [candles, volumes]);

  return (
    <ChartWithDataSheet rows={rows}>
      <div ref={containerRef} className="h-72 w-full" />
    </ChartWithDataSheet>
  );
}

function TimeseriesLineChart({ rows }: Readonly<{ rows: Record<string, unknown>[] }>): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const metricKeys = numericKeysIn(rows).slice(0, 6);
  if (rows.length === 0 || metricKeys.length === 0) {
    return null;
  }

  const points = rows.map((row) => {
    const point: Record<string, unknown> = { date: rowDate(row) ?? "" };
    for (const key of metricKeys) {
      point[key] = asNumber(row[key]);
    }
    return point;
  });

  return (
    <ChartWithDataSheet rows={rows}>
      <div className="mb-2 flex flex-wrap gap-3">
        {metricKeys.map((key, index) => (
          <span
            key={key}
            className="inline-flex items-center gap-1.5 text-[12px] text-[var(--text-muted)]"
          >
            <span
              aria-hidden
              className="inline-block size-2 rounded-full"
              style={{ backgroundColor: LINE_COLORS[index % LINE_COLORS.length] }}
            />
            {fieldLabel(t, key)}
          </span>
        ))}
      </div>
      <div style={{ width: "100%", height: 256 }}>
        <ResponsiveContainer>
          <LineChart data={points}>
            <CartesianGrid stroke="var(--grid)" />
            <XAxis
              dataKey="date"
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              stroke="var(--border)"
            />
            <YAxis
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              tickFormatter={(v: number) => formatCompactNumber(v, locale)}
              width={56}
              stroke="var(--border)"
            />
            <Tooltip
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              labelStyle={{ color: "var(--text)" }}
              itemStyle={{ color: "var(--text)" }}
              cursor={{ fill: "var(--accent)", opacity: 0.08 }}
              formatter={(value) => formatNumber(asNumber(value), locale)}
            />
            {metricKeys.map((key, index) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={LINE_COLORS[index % LINE_COLORS.length] ?? "var(--chart-1)"}
                dot={false}
                strokeWidth={2}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

function extractRows(data: unknown): Record<string, unknown>[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const record = data as Record<string, unknown>;
  // Most timeseries payloads key their row list "rows"; a few bundle multiple named panels
  // (macro_indicators, cb_flow, price_multi_tf) — the first array-valued field found is used,
  // which covers the common single-panel case; multi-panel bundles render their first panel
  // until a dedicated multi-panel layout is built.
  if (Array.isArray(record.rows)) {
    return asArray(record.rows);
  }
  for (const value of Object.values(record)) {
    if (Array.isArray(value)) {
      return asArray(value);
    }
  }
  return [];
}
