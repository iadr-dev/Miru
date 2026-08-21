"use client";

import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  asArray,
  asNumber,
  asString,
  formatNumber,
  rowDate,
} from "@/app/[locale]/(app)/chat/cards/card-format";
import { ChartWithDataSheet } from "@/app/[locale]/(app)/chat/cards/chart-with-data-sheet";

const NAME_KEYS = ["sector", "name", "industry", "investor", "holding_shares_level", "bank_name"];
const VALUE_KEYS = ["weight", "trading_money_pct", "percent", "net_shares_flow", "buy", "sell"];

/** FinMind's `shareholder_dispersion` (`TaiwanStockHoldingSharesPer`) 股權分散表 rows include a
 * `total` aggregate bucket (≈ the sum of every real tier) and a `差異數調整（說明4）` reconciliation
 * row — neither is a real shareholding tier, so both dwarf the genuine buckets into invisible
 * slivers if left in the chart (confirmed live: `total`'s value is roughly 100%, `more than
 * 1,000,001`'s own real tier is comparatively tiny). Excluded by exact label match rather than a
 * threshold — a genuinely huge real tier (common for concentrated large-caps) must never be
 * mistaken for the aggregate row. */
const AGGREGATE_CATEGORY_LABELS = new Set(["total", "差異數調整（說明4）"]);

const BAR_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
];

/** A total broken into named buckets at one point in time (`industry_money_flow`,
 * `shareholder_dispersion`, `fund_holdings`' sector slice, `derivatives_institutional`'s
 * per-category positioning) — rendered as a horizontal bar chart, one bar per named category,
 * since the category count varies (a handful to a few dozen) and a horizontal layout keeps long
 * Chinese category labels legible without rotation. */
export function FlowDistribution({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const { rows: allRows, isSectorWeighting } = extractRows(data);
  const nameKey = firstPresentKey(allRows, NAME_KEYS);
  const valueKey = firstPresentKey(allRows, VALUE_KEYS);
  if (allRows.length === 0 || !nameKey || !valueKey) {
    return null;
  }
  // `fund_holdings`'s `sector_weightings` slice stores `weight` as a 0-1 fraction (confirmed
  // against `etf_overlap_tools.py::_sector_weighting_strings`'s own `row.weight * 100`
  // convention) — unlike this same component's `holdings` slice, whose `weight` is already
  // 0-100-scaled at the source. Scale only the sector-weighting case, not every `weight` value.
  const scale = isSectorWeighting && valueKey === "weight" ? 100 : 1;

  // A date-scoped distribution (e.g. `shareholder_dispersion`, queried over a range) carries one
  // full bucket set PER report date — flattening every date together would duplicate every
  // category label across however many dates were returned. Only the most recent date's buckets
  // describe "the distribution" as of now, in both the chart and its own data-sheet toggle below
  // (which mirrors exactly what the chart plots, per `ChartWithDataSheet`'s contract) — a
  // single-snapshot distribution (no date field at all) is unaffected, every row is kept.
  const latestDate = allRows.reduce<string | null>((latest, row) => {
    const d = rowDate(row);
    return d && (!latest || d > latest) ? d : latest;
  }, null);
  const rows = latestDate
    ? allRows.filter((row) => rowDate(row) === latestDate || rowDate(row) === null)
    : allRows;

  const points = rows
    .map((row) => ({
      name: asString(row[nameKey]) ?? "—",
      value: (asNumber(row[valueKey]) ?? 0) * scale,
    }))
    .filter((point) => !AGGREGATE_CATEGORY_LABELS.has(point.name))
    .slice(0, 20);
  const sheetRows = points.map((point) => ({ name: point.name, [valueKey]: point.value }));

  return (
    <ChartWithDataSheet rows={sheetRows}>
      <div style={{ width: "100%", height: Math.max(160, points.length * 28) }}>
        <ResponsiveContainer>
          <BarChart data={points} layout="vertical" margin={{ left: 8, right: 24 }}>
            <CartesianGrid horizontal={false} stroke="var(--grid)" />
            <XAxis
              type="number"
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              tickFormatter={(v: number) => formatNumber(v, locale)}
              stroke="var(--border)"
            />
            <YAxis
              type="category"
              dataKey="name"
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              width={110}
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
            <Bar dataKey="value" radius={4}>
              {points.map((point, index) => (
                // `point.name` alone collides when two rows share the same category label (e.g.
                // a shareholding-tier row and a "差異數調整" correction row both labeled "1-999")
                // — the index breaks that tie. Safe here: `points` is a fixed, never-reordered
                // snapshot from one render's `data` prop.
                // biome-ignore lint/suspicious/noArrayIndexKey: tie-breaker only, see comment above.
                <Cell key={`${point.name}|${index}`} fill={BAR_COLORS[index % BAR_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

function firstPresentKey(rows: Record<string, unknown>[], candidates: string[]): string | null {
  const first = rows[0];
  if (!first) {
    return null;
  }
  return candidates.find((key) => key in first) ?? null;
}

function extractRows(data: unknown): {
  rows: Record<string, unknown>[];
  isSectorWeighting: boolean;
} {
  if (!data || typeof data !== "object") {
    return { rows: [], isSectorWeighting: false };
  }
  const record = data as Record<string, unknown>;
  if (Array.isArray(record.holdings)) {
    return { rows: asArray(record.holdings), isSectorWeighting: false };
  }
  if (Array.isArray(record.sector_weightings)) {
    return { rows: asArray(record.sector_weightings), isSectorWeighting: true };
  }
  for (const value of Object.values(record)) {
    if (Array.isArray(value)) {
      return { rows: asArray(value), isSectorWeighting: false };
    }
  }
  return { rows: [], isSectorWeighting: false };
}
