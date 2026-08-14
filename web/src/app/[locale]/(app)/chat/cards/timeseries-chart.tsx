"use client";

import {
  type CandlestickData,
  CandlestickSeries,
  createChart,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { useLocale } from "next-intl";
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
import {
  asArray,
  asNumber,
  formatNumber,
  rowDate,
} from "@/app/[locale]/(app)/chat/cards/card-format";

/** A row is candlestick-eligible once it carries all four OHLC fields — this is the one shape
 * TradingView-style price/OHLCV charting is actually meant for (per lightweight-charts' own
 * scope: https://www.tradingview.com/x/xTW9HDQE/). Everything else in the timeseries bucket
 * (ratios, revenue, flows, ...) is multi-metric line data, not a price chart, and renders via
 * recharts below instead. */
function toCandles(rows: Record<string, unknown>[]): CandlestickData<Time>[] {
  const points: CandlestickData<Time>[] = [];
  for (const row of rows) {
    const time = rowDate(row);
    const open = asNumber(row.open);
    const high = asNumber(row.high);
    const low = asNumber(row.low);
    const close = asNumber(row.close);
    if (time && open !== null && high !== null && low !== null && close !== null) {
      points.push({ time: time as Time, open, high, low, close });
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

/** Resolves a CSS custom property to its computed color — `lightweight-charts` draws to canvas
 * and can't read `var(--chart-1)` itself, so the token has to be resolved to a real color string
 * before being handed to the chart's options. */
function resolveToken(token: string): string {
  if (typeof window === "undefined") {
    return "#8a93a3";
  }
  const match = /var\((--[a-z0-9-]+)\)/.exec(token);
  if (!match) {
    return token;
  }
  const varName = match[1];
  if (!varName) {
    return token;
  }
  return getComputedStyle(document.documentElement).getPropertyValue(varName).trim() || token;
}

/** Renders a candlestick-eligible timeseries card as a real price chart via `lightweight-charts`
 * (the one place in the chat surface that isn't plain React-rendered DOM, mirroring how the
 * project already scopes canvas-owning libraries per web/OVERVIEW.md's "lightweight-charts for
 * candles" call) — everything else in the timeseries bucket renders as a `recharts` multi-line
 * chart (`TimeseriesLineChart` below), since it isn't price/OHLCV data. */
export function TimeseriesChart({ data }: { data: unknown }): ReactNode {
  const rows = extractRows(data);
  const candles = toCandles(rows);

  if (candles.length > 0) {
    return <CandlestickChart candles={candles} />;
  }
  return <TimeseriesLineChart rows={rows} />;
}

function CandlestickChart({ candles }: { candles: CandlestickData<Time>[] }): ReactNode {
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
    series.setData(candles);
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [candles]);

  return (
    <div className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4">
      <div ref={containerRef} className="h-64 w-full" />
    </div>
  );
}

function TimeseriesLineChart({ rows }: { rows: Record<string, unknown>[] }): ReactNode {
  const locale = useLocale();
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
    <div className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4">
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
            {key}
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
              tickFormatter={(v: number) => formatNumber(v, locale)}
              stroke="var(--border)"
            />
            <Tooltip
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              formatter={(value) => formatNumber(asNumber(value), locale)}
            />
            {metricKeys.map((key, index) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={LINE_COLORS[index % LINE_COLORS.length]}
                dot={false}
                strokeWidth={2}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
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
