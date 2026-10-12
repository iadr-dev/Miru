"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  Bar,
  CartesianGrid,
  Legend,
  BarChart as RechartsBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartWithDataSheet } from "@/components/ui/chart-with-data-sheet";
import { fieldLabel } from "@/lib/field-labels";
import { asArray, asNumber, formatCompactNumber, formatNumber, rowDate } from "@/lib/format-number";

/** Fields that are date/id metadata rather than a plottable metric — same exclusion set
 * `TimeseriesLineChart` uses, kept in sync manually (see that module for why). */
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

const BAR_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
];

/** Discrete, one-per-period filings (月營收, 除權除息結果) plotted as vertical bars rather than a
 * continuous line — a monthly/event value connected point-to-point misreads as a smooth trend
 * between filings that never actually happened continuously. Distinct from `GroupedBarChart`
 * (exactly 2 genuinely parallel tracks, e.g. margin vs short balance) and `DivergingBarChart`
 * (a signed net metric) — this is 1-6 independent unsigned magnitude series, one bar per period,
 * categorical `--chart-1..6` coloring throughout. */
export function BarChart({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const rows = extractRows(data);
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
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <RechartsBarChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
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
              labelFormatter={(key) => String(key)}
            />
            {metricKeys.length > 1 ? (
              <Legend
                formatter={(value) => fieldLabel(t, String(value))}
                wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }}
              />
            ) : null}
            {metricKeys.map((key, index) => (
              <Bar
                key={key}
                dataKey={key}
                name={key}
                fill={BAR_COLORS[index % BAR_COLORS.length]}
                radius={4}
              />
            ))}
          </RechartsBarChart>
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
