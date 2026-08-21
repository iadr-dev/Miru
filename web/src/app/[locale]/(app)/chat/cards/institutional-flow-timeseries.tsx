"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import {
  Bar,
  CartesianGrid,
  BarChart as RechartsBarChart,
  ReferenceLine,
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
} from "@/app/[locale]/(app)/chat/cards/card-format";
import { ChartWithDataSheet } from "@/app/[locale]/(app)/chat/cards/chart-with-data-sheet";
import { fieldLabel } from "@/app/[locale]/(app)/chat/cards/field-labels";
import { TableWithActions } from "@/app/[locale]/(app)/chat/table-actions";
import { cn } from "@/lib/cn";

/** `institutional_flow`'s raw row shape is `{trade_date, investor, buy, sell}` — one row per
 * (date, category) pair, `investor` a PascalCase category name ("Foreign_Investor",
 * "Investment_Trust", ...), NOT a set of per-category numeric columns. Plotting these rows
 * through a plain multi-series chart (`BarChart`) treats `investor`'s string values as if they
 * were numeric-series data, which is exactly the raw-category-name spam a live QA pass caught —
 * this component pivots the rows into one stacked/grouped bar per `trade_date`, matching the
 * reference 三大法人買賣超 mobile UI (date-indexed stacked columns, a toggleable per-category
 * legend, a zero baseline, plus a totals-and-per-day table below). */

const CATEGORY_ORDER = [
  "Foreign_Investor",
  "Investment_Trust",
  "Dealer_self",
  "Dealer_Hedging",
  "Foreign_Dealer_Self",
] as const;

const CATEGORY_COLORS: Record<string, string> = {
  Foreign_Investor: "var(--chart-1)",
  Investment_Trust: "var(--chart-2)",
  Dealer_self: "var(--chart-3)",
  Dealer_Hedging: "var(--chart-4)",
  Foreign_Dealer_Self: "var(--chart-5)",
};

/** `fieldLabel`'s `LABELED_FIELDS` set is keyed lowercase (`foreign_investor`), but the raw
 * `investor` row value arrives PascalCase (`Foreign_Investor`) — lowercase before lookup rather
 * than adding a second, duplicate-cased entry per category to the shared label map. */
function categoryLabel(t: ReturnType<typeof useTranslations>, investor: string): string {
  return fieldLabel(t, investor.toLowerCase());
}

type DatePoint = { date: string; total: number } & Record<string, number>;

function pivotByDate(rows: Record<string, unknown>[]): {
  points: DatePoint[];
  categories: string[];
} {
  const byDate = new Map<string, Record<string, number>>();
  const categoriesSeen = new Set<string>();

  for (const row of rows) {
    const date = asString(row.trade_date);
    const investor = asString(row.investor);
    const buy = asNumber(row.buy);
    const sell = asNumber(row.sell);
    if (!date || !investor || buy === null || sell === null) {
      continue;
    }
    categoriesSeen.add(investor);
    const perDate = byDate.get(date) ?? {};
    // Rows are already one-per-(date,category) — `+=` only matters if the backend ever returns
    // a duplicate for the same pair, which would otherwise silently overwrite instead of summing.
    perDate[investor] = (perDate[investor] ?? 0) + (buy - sell);
    byDate.set(date, perDate);
  }

  const categories = CATEGORY_ORDER.filter((c) => categoriesSeen.has(c));
  const points: DatePoint[] = [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, perDate]) => {
      const point: Record<string, number> = {};
      let total = 0;
      for (const category of categories) {
        const net = perDate[category] ?? 0;
        point[category] = net;
        total += net;
      }
      return { date, total, ...point } as DatePoint;
    });

  return { points, categories };
}

export function InstitutionalFlowTimeseries({ data }: { data: unknown }): ReactNode {
  const locale = useLocale();
  const t = useTranslations("chat.cards");
  const rows = extractRows(data);
  const { points, categories } = useMemo(() => pivotByDate(rows), [rows]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  if (points.length === 0 || categories.length === 0) {
    return null;
  }

  const visible = categories.filter((c) => !hidden.has(c));
  const totals = categories.reduce<Record<string, { buy: number; sell: number }>>(
    (acc, category) => {
      acc[category] = { buy: 0, sell: 0 };
      return acc;
    },
    {},
  );
  for (const row of rows) {
    const investor = asString(row.investor);
    const buy = asNumber(row.buy);
    const sell = asNumber(row.sell);
    if (!investor || !totals[investor] || buy === null || sell === null) continue;
    totals[investor].buy += buy;
    totals[investor].sell += sell;
  }

  const sheetRows = points.map((point) => {
    const sheetRow: Record<string, unknown> = { trade_date: point.date };
    for (const category of categories) {
      sheetRow[category] = point[category];
    }
    return sheetRow;
  });

  return (
    <ChartWithDataSheet rows={sheetRows}>
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
            <ReferenceLine y={0} stroke="var(--border)" />
            <Tooltip
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              formatter={(value) => formatNumber(asNumber(value), locale)}
              labelFormatter={(key) => String(key)}
            />
            {visible.map((category) => (
              <Bar
                key={category}
                dataKey={category}
                // `name` (not `dataKey`) is what Recharts uses for both the legend swatch's own
                // accessible label ("{name} legend icon") and the tooltip row label — passing
                // the raw category key here (as opposed to translating only through a
                // `formatter`, which leaves the icon's generated label untouched) was a live QA
                // finding: screen readers/hover tooltips announced "Foreign_Investor" instead of
                // "外資". Translating at the source here fixes both surfaces at once.
                name={categoryLabel(t, category)}
                stackId="net"
                fill={CATEGORY_COLORS[category] ?? "var(--chart-6)"}
                radius={2}
              />
            ))}
          </RechartsBarChart>
        </ResponsiveContainer>
      </div>
      {/* A checkbox legend, not Recharts' built-in click-to-toggle `<Legend>` — that swatch gave
       * no visible checked/unchecked state (a live QA finding: clicking hid the bar, but the
       * legend itself looked identical either way, so there was no way to tell which categories
       * were currently on without hovering the chart itself). */}
      <div
        className="mt-3 flex flex-wrap gap-3"
        role="group"
        aria-label={t("fields.investor_totals")}
      >
        {categories.map((category) => {
          const isVisible = !hidden.has(category);
          return (
            <label
              key={category}
              className="flex min-h-11 items-center gap-1.5 text-[13px] text-[var(--text)]"
            >
              <input
                type="checkbox"
                checked={isVisible}
                onChange={() => {
                  setHidden((prev) => {
                    const next = new Set(prev);
                    if (next.has(category)) {
                      next.delete(category);
                    } else {
                      next.add(category);
                    }
                    return next;
                  });
                }}
                style={{ accentColor: CATEGORY_COLORS[category] ?? "var(--chart-6)" }}
                className={cn(
                  "size-4 rounded border-[var(--border)]",
                  "!outline-none focus-visible:rounded-[3px] focus-visible:ring-2",
                  "focus-visible:ring-[var(--accent)] focus-visible:ring-offset-1",
                )}
              />
              {categoryLabel(t, category)}
            </label>
          );
        })}
      </div>
      <div className="mt-3">
        <TableWithActions className="text-[13px]">
          <thead>
            <tr className="bg-[var(--surface-2)]">
              <th className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]">
                {t("fields.investor_totals")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("fields.buy")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("fields.sell")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {t("fields.net")}
              </th>
            </tr>
          </thead>
          <tbody>
            {categories.map((category) => {
              const total = totals[category] ?? { buy: 0, sell: 0 };
              const net = total.buy - total.sell;
              return (
                <tr key={category}>
                  <td className="border-[var(--border)] border-b px-3 py-2 text-[var(--text)]">
                    {categoryLabel(t, category)}
                  </td>
                  <td className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums">
                    {formatNumber(total.buy, locale)}
                  </td>
                  <td className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums">
                    {formatNumber(total.sell, locale)}
                  </td>
                  <td
                    className="border-[var(--border)] border-b px-3 py-2 text-right font-num tabular-nums"
                    style={{ color: net >= 0 ? "var(--up)" : "var(--down)" }}
                    aria-label={
                      net >= 0
                        ? `${categoryLabel(t, category)} ▲ ${formatNumber(net, locale)}`
                        : `${categoryLabel(t, category)} ▼ ${formatNumber(net, locale)}`
                    }
                  >
                    {net >= 0 ? "▲ " : "▼ "}
                    {formatNumber(net, locale)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </TableWithActions>
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
  return [];
}
