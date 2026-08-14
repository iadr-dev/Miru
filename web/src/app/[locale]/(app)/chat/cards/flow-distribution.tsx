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
} from "@/app/[locale]/(app)/chat/cards/card-format";

const NAME_KEYS = ["sector", "name", "industry", "investor", "holding_shares_level", "bank_name"];
const VALUE_KEYS = ["weight", "trading_money_pct", "percent", "net_shares_flow", "buy", "sell"];

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
  const rows = extractRows(data);
  const nameKey = firstPresentKey(rows, NAME_KEYS);
  const valueKey = firstPresentKey(rows, VALUE_KEYS);
  if (rows.length === 0 || !nameKey || !valueKey) {
    return null;
  }

  const points = rows
    .map((row) => ({ name: asString(row[nameKey]) ?? "—", value: asNumber(row[valueKey]) ?? 0 }))
    .slice(0, 20);

  return (
    <div className="mt-3 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-4">
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
                <Cell key={point.name} fill={BAR_COLORS[index % BAR_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function firstPresentKey(rows: Record<string, unknown>[], candidates: string[]): string | null {
  const first = rows[0];
  if (!first) {
    return null;
  }
  return candidates.find((key) => key in first) ?? null;
}

function extractRows(data: unknown): Record<string, unknown>[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const record = data as Record<string, unknown>;
  if (Array.isArray(record.holdings)) {
    return asArray(record.holdings);
  }
  if (Array.isArray(record.sector_weightings)) {
    return asArray(record.sector_weightings);
  }
  for (const value of Object.values(record)) {
    if (Array.isArray(value)) {
      return asArray(value);
    }
  }
  return [];
}
