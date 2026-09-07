"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
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
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { fieldLabel } from "@/app/[locale]/(app)/chat/cards/field-labels";
import { TableWithActions } from "@/app/[locale]/(app)/chat/table-actions";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import type { InstitutionalFlowTotalMarketRow } from "@/types/market";

/** The categories worth their own bar series — `total` is excluded from the chart (it would
 * dwarf every per-category bar on the same axis) but still shown in the summary table below. */
const CHART_CATEGORIES = ["Foreign_Investor", "Investment_Trust", "Dealer_self", "Dealer_Hedging"];
const CATEGORY_FIELD_KEY: Record<string, string> = {
  Foreign_Investor: "foreign_investor",
  Investment_Trust: "investment_trust",
  Dealer_self: "dealer_self",
  Dealer_Hedging: "dealer_hedging",
  Foreign_Dealer_Self: "foreign_dealer_self",
  Dealer: "dealer",
};

const HISTORY_PREVIEW_DAYS = 10;

/** 三大法人買賣超 — real multi-day history from `get_institutional_flow_total_market`
 * (`InstitutionalFlowTotalMarketRow`, one row per (trade_date, category)), rendered as a grouped
 * bar chart over the last `HISTORY_PREVIEW_DAYS` trading days plus a per-category summary table
 * for the latest day — never a single-day snapshot only, since the backend already returns the
 * full range the caller requested. */
export function InstitutionalFlowCard({
  rows,
}: {
  rows: readonly InstitutionalFlowTotalMarketRow[];
}): ReactNode {
  const t = useTranslations("chat.cards");
  const tCard = useTranslations("market.cards.institutionalRanking");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  // Clicking a legend entry toggles that category's bar series off/on — a real interactive
  // legend (recharts' documented pattern), not just a static color key.
  const [hiddenCategories, setHiddenCategories] = useState<ReadonlySet<string>>(new Set());
  const toggleCategory = (category: string): void => {
    setHiddenCategories((prev) => {
      const next = new Set(prev);
      if (next.has(category)) {
        next.delete(category);
      } else {
        next.add(category);
      }
      return next;
    });
  };

  const dates = [...new Set(rows.map((r) => r.trade_date))].sort();
  const latestDate = dates.at(-1);
  const latestRows = rows.filter((r) => r.trade_date === latestDate);
  const previewDates = dates.slice(-HISTORY_PREVIEW_DAYS);

  const chartData = previewDates.map((date) => {
    const point: Record<string, unknown> = { date };
    for (const category of CHART_CATEGORIES) {
      const row = rows.find((r) => r.trade_date === date && r.name === category);
      point[category] = row ? row.buy - row.sell : 0;
    }
    return point;
  });

  const chart = (
    <div className="min-h-[220px] flex-1" style={{ width: "100%" }}>
      <ResponsiveContainer>
        <BarChart data={chartData} margin={{ left: 8, right: 8 }}>
          <CartesianGrid stroke="var(--grid)" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            stroke="var(--border)"
          />
          <YAxis
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
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
            formatter={(value, name) => [
              formatNumber(value as number, locale),
              fieldLabel(t, CATEGORY_FIELD_KEY[name as string] ?? (name as string)),
            ]}
          />
          <Legend
            content={() => (
              <ul className="mt-2 flex flex-wrap justify-center gap-3">
                {CHART_CATEGORIES.map((category, index) => {
                  const isHidden = hiddenCategories.has(category);
                  return (
                    <li key={category}>
                      <button
                        type="button"
                        onClick={() => toggleCategory(category)}
                        className="inline-flex items-center gap-1.5 text-[12px]"
                        style={{ color: isHidden ? "var(--text-muted)" : "var(--text)" }}
                      >
                        <span
                          aria-hidden
                          className="inline-block size-2.5 rounded-sm"
                          style={{
                            backgroundColor: isHidden
                              ? "var(--text-muted)"
                              : `var(--chart-${(index % 6) + 1})`,
                          }}
                        />
                        {fieldLabel(t, CATEGORY_FIELD_KEY[category] ?? category)}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          />
          {CHART_CATEGORIES.map((category, index) => (
            <Bar
              key={category}
              dataKey={category}
              fill={`var(--chart-${(index % 6) + 1})`}
              radius={3}
              hide={hiddenCategories.has(category)}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );

  return (
    <section className="flex h-full w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{tCard("title")}</h2>
      {chartData.length > 0 ? chart : null}

      {latestRows.length > 0 ? (
        <div className="mt-4">
          <div className="grid grid-cols-[1.4fr_1fr_1fr_1fr] items-center gap-2 border-[var(--border)] border-b pb-1 text-[11px] text-[var(--text-muted)]">
            <span>{tCard("category")}</span>
            <span className="text-right">{tCard("buy")}</span>
            <span className="text-right">{tCard("sell")}</span>
            <span className="text-right">{tCard("net")}</span>
          </div>
          <ul className="divide-y divide-[var(--border)]">
            {latestRows.map((row) => {
              const net = row.buy - row.sell;
              return (
                <li
                  key={row.name}
                  className="grid grid-cols-[1.4fr_1fr_1fr_1fr] items-center gap-2 py-1 text-[12.5px]"
                >
                  <span className="text-[var(--text-muted)]">
                    {fieldLabel(t, CATEGORY_FIELD_KEY[row.name] ?? row.name)}
                  </span>
                  <span className="text-right font-num text-[var(--text)] tabular-nums">
                    {formatNumber(row.buy, locale)}
                  </span>
                  <span className="text-right font-num text-[var(--text)] tabular-nums">
                    {formatNumber(row.sell, locale)}
                  </span>
                  <span
                    className="text-right font-num tabular-nums"
                    style={{ color: net >= 0 ? "var(--up)" : "var(--down)" }}
                  >
                    {net >= 0 ? "▲" : "▼"}
                    {formatNumber(Math.abs(net), locale)}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ) : null}

      {dates.length > 1 ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={cn(
            "mt-auto flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          <span>{tCard("expandHistory")}</span>
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={tCard("title")}
        closeLabel={tCard("collapseHistory")}
        onClose={() => setExpanded(false)}
      >
        <TableWithActions className="text-[13px]">
          <thead>
            <tr className="bg-[var(--surface-2)]">
              <th className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]">
                {tCard("historyDate")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-left font-medium text-[var(--text-muted)]">
                {tCard("category")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {tCard("buy")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {tCard("sell")}
              </th>
              <th className="border-[var(--border)] border-b px-3 py-2 text-right font-medium text-[var(--text-muted)]">
                {tCard("net")}
              </th>
            </tr>
          </thead>
          <tbody>
            {[...rows]
              .reverse()
              .slice(0, 200)
              .map((row) => {
                const net = row.buy - row.sell;
                return (
                  <tr key={`${row.trade_date}-${row.name}`}>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-[var(--text)]">
                      {row.trade_date}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-[var(--text)]">
                      {fieldLabel(t, CATEGORY_FIELD_KEY[row.name] ?? row.name)}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums">
                      {formatNumber(row.buy, locale)}
                    </td>
                    <td className="border-[var(--border)] border-b px-3 py-2 text-right font-num text-[var(--text)] tabular-nums">
                      {formatNumber(row.sell, locale)}
                    </td>
                    <td
                      className="border-[var(--border)] border-b px-3 py-2 text-right font-num tabular-nums"
                      style={{ color: net >= 0 ? "var(--up)" : "var(--down)" }}
                    >
                      {net >= 0 ? "▲" : "▼"}
                      {formatNumber(Math.abs(net), locale)}
                    </td>
                  </tr>
                );
              })}
          </tbody>
        </TableWithActions>
      </ExpandModal>
    </section>
  );
}
