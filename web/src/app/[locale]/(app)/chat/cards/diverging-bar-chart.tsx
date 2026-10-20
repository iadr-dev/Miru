"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ChartWithDataSheet } from "@/components/ui/chart-with-data-sheet";
import { fieldLabel } from "@/lib/field-labels";
import { asArray, asNumber, formatNumber } from "@/lib/format-number";

/** A `buy`/`sell` field pair this shape knows how to net into one signed bar — each entry names
 * the raw backend field prefix (e.g. `foreign_investor` -> `foreign_investor_buy`/`_sell`) and
 * the category label key `fieldLabel` resolves it through. Covers `institutional_flow_wide`
 * (per-category buy/sell columns, one row per trade_date) and `institutional_flow_total_market`
 * (one row per category `name`, plain `buy`/`sell`) — the two backend shapes this renders. */
const CATEGORY_PREFIXES = [
  "foreign_investor",
  "investment_trust",
  "dealer",
  "dealer_self",
  "dealer_hedging",
  "foreign_dealer_self",
] as const;

type NetPoint = { name: string; net: number };

/** `institutional_flow_wide`-shaped: one row (typically the latest trade_date) with a
 * `{prefix}_buy`/`{prefix}_sell` column pair per institutional-investor category — nets each
 * pair into one signed bar. Governance: `--up`/`--down` per the signed-series rule
 * (signed vs categorical) — this IS a signed net-flow series, unlike
 * `FlowDistribution`'s categorical bars, so `--chart-1..6` would be the wrong token set here. */
function netPointsFromWideRow(row: Record<string, unknown>): NetPoint[] {
  const points: NetPoint[] = [];
  for (const prefix of CATEGORY_PREFIXES) {
    const buy = asNumber(row[`${prefix}_buy`]);
    const sell = asNumber(row[`${prefix}_sell`]);
    if (buy === null || sell === null) {
      continue;
    }
    points.push({ name: prefix, net: buy - sell });
  }
  return points;
}

/** `institutional_flow_total_market`-shaped: many rows, one per category `name`, each with a
 * plain `buy`/`sell` — nets each row directly rather than pivoting on a fixed prefix list, since
 * `name` here is already the category label (e.g. "外資", "投信", "自營商"). */
function netPointsFromCategoryRows(rows: Record<string, unknown>[]): NetPoint[] {
  const points: NetPoint[] = [];
  for (const row of rows) {
    const name = row.name;
    const buy = asNumber(row.buy);
    const sell = asNumber(row.sell);
    if (typeof name !== "string" || buy === null || sell === null) {
      continue;
    }
    points.push({ name, net: buy - sell });
  }
  return points;
}

/** A signed net buy/sell metric per named institutional-investor category (三大法人買賣超) —
 * one diverging bar per category, extending right (net buy) or left (net sell) of a zero
 * baseline. Distinct from `FlowDistribution`'s unsigned category bars: this metric is genuinely
 * signed (紅漲綠跌 territory), so bars use `--up`/`--down` with an explicit `aria-label` carrying
 * direction + value, never color alone. */
export function DivergingBarChart({ data }: Readonly<{ data: unknown }>): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const points = extractNetPoints(data);
  if (points.length === 0) {
    return null;
  }
  const sheetRows = points.map((point) => ({ name: point.name, net: point.net }));

  return (
    <ChartWithDataSheet rows={sheetRows}>
      <div style={{ width: "100%", height: Math.max(160, points.length * 32) }}>
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
              tickFormatter={(key: string) => fieldLabel(t, key)}
              tick={{ fill: "var(--text-muted)", fontSize: 12 }}
              width={110}
              stroke="var(--border)"
            />
            <ReferenceLine x={0} stroke="var(--border)" />
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
              labelFormatter={(key) => fieldLabel(t, String(key))}
              formatter={(value) => formatNumber(asNumber(value), locale)}
            />
            <Bar dataKey="net" radius={4}>
              {points.map((point, index) => (
                <Cell
                  // `point.name` alone can collide if two entities share a label — the index
                  // breaks that tie. Safe here: `points` is a fixed, never-reordered snapshot.
                  // biome-ignore lint/suspicious/noArrayIndexKey: tie-breaker only, see comment above.
                  key={`${point.name}|${index}`}
                  fill={point.net >= 0 ? "var(--up)" : "var(--down)"}
                  aria-label={
                    point.net >= 0
                      ? `${fieldLabel(t, point.name)} ▲ ${formatNumber(point.net, locale)}`
                      : `${fieldLabel(t, point.name)} ▼ ${formatNumber(point.net, locale)}`
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

function extractNetPoints(data: unknown): NetPoint[] {
  if (!data || typeof data !== "object") {
    return [];
  }
  const record = data as Record<string, unknown>;
  const rows = Array.isArray(record.rows) ? asArray(record.rows) : [];
  if (rows.length === 0) {
    return [];
  }
  // Prefer the category-rows shape (institutional_flow_total_market) when the first row already
  // carries a `name` field — otherwise treat it as the wide, one-row-per-date shape
  // (institutional_flow_wide) and net its fixed buy/sell column pairs from the latest row.
  const first = rows[0];
  if (first && typeof first.name === "string") {
    return netPointsFromCategoryRows(rows);
  }
  return first ? netPointsFromWideRow(first) : [];
}
