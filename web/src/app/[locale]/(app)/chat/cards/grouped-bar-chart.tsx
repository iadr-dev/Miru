"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  asArray,
  asNumber,
  asString,
  formatCompactNumber,
  formatNumber,
  rowDate,
} from "@/app/[locale]/(app)/chat/cards/card-format";
import { ChartWithDataSheet } from "@/app/[locale]/(app)/chat/cards/chart-with-data-sheet";
import { fieldLabel } from "@/app/[locale]/(app)/chat/cards/field-labels";

type GroupedPoint = { category: string; a: number; b: number };
type GroupedSeries = { aKey: string; bKey: string; points: GroupedPoint[] };

/** `margin_short`-shaped: one row per trade_date, two unsigned balance tracks
 * (`margin_purchase_today_balance` vs `short_sale_today_balance`) — genuinely parallel
 * magnitudes, not a signed net, so this is `--chart-1`/`--chart-2` categorical territory, never
 * `--up`/`--down` (the signed-vs-categorical rule). */
function marginShortSeries(rows: Record<string, unknown>[]): GroupedSeries | null {
  const points: GroupedPoint[] = [];
  for (const row of rows) {
    const category = rowDate(row);
    const margin = asNumber(row.margin_purchase_today_balance);
    const short = asNumber(row.short_sale_today_balance);
    if (category === null || margin === null || short === null) {
      continue;
    }
    points.push({ category, a: margin, b: short });
  }
  return points.length > 0
    ? { aKey: "margin_purchase_today_balance", bKey: "short_sale_today_balance", points }
    : null;
}

/** `put_call_ratio`-shaped: `OptionDailyRow` rows under `options`, one row per (strike, call/put,
 * trade_date) — pivoted to one point per strike with a call-volume/put-volume pair, so calls and
 * puts render as two bars side by side at each strike (the grouped-by-strike view options
 * positioning is usually read as, per the "options OI/max pain" TW-site pattern). Only the
 * latest trade_date's rows are used — a multi-date series would need a date picker this card
 * doesn't have yet. */
function putCallSeries(rows: Record<string, unknown>[]): GroupedSeries | null {
  const latestDate = rows.reduce<string | null>((latest, row) => {
    const d = rowDate(row);
    return d && (!latest || d > latest) ? d : latest;
  }, null);
  const byStrike = new Map<number, { call: number; put: number }>();
  for (const row of rows) {
    if (rowDate(row) !== latestDate) {
      continue;
    }
    const strike = asNumber(row.strike_price);
    const volume = asNumber(row.volume);
    const callPut = asString(row.call_put);
    if (strike === null || volume === null || !callPut) {
      continue;
    }
    const entry = byStrike.get(strike) ?? { call: 0, put: 0 };
    if (callPut.toLowerCase().startsWith("c")) {
      entry.call += volume;
    } else {
      entry.put += volume;
    }
    byStrike.set(strike, entry);
  }
  const points: GroupedPoint[] = [...byStrike.entries()]
    .sort(([a], [b]) => a - b)
    .map(([strike, { call, put }]) => ({ category: String(strike), a: call, b: put }));
  return points.length > 0 ? { aKey: "call_volume", bKey: "put_volume", points } : null;
}

/** Two genuinely parallel numeric tracks per category (margin balance vs short-sale balance;
 * call volume vs put volume by strike) — grouped bars, one pair per category, both series
 * categorical (`--chart-1`/`--chart-2`), never signed (`--up`/`--down` is reserved for the
 * diverging net-flow shape, `DivergingBarChart`, not this one). */
export function GroupedBarChart({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const series = extractSeries(data);
  if (!series) {
    return null;
  }
  const { aKey, bKey, points } = series;
  const sheetRows = points.map((point) => ({
    category: point.category,
    [aKey]: point.a,
    [bKey]: point.b,
  }));

  return (
    <ChartWithDataSheet rows={sheetRows}>
      <div style={{ width: "100%", height: 280 }}>
        <ResponsiveContainer>
          <BarChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="category"
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
            <Legend
              formatter={(value) => fieldLabel(t, String(value))}
              wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }}
            />
            <Bar dataKey="a" name={aKey} fill="var(--chart-1)" radius={4} />
            <Bar dataKey="b" name={bKey} fill="var(--chart-2)" radius={4} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

function extractSeries(data: unknown): GroupedSeries | null {
  if (!data || typeof data !== "object") {
    return null;
  }
  const record = data as Record<string, unknown>;
  if (Array.isArray(record.rows)) {
    const rows = asArray(record.rows);
    const margin = marginShortSeries(rows);
    if (margin) {
      return margin;
    }
  }
  if (Array.isArray(record.options)) {
    return putCallSeries(asArray(record.options));
  }
  return null;
}
