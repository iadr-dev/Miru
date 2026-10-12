"use client";

import { useLocale } from "next-intl";
import type { ReactNode } from "react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { formatNumber } from "@/lib/format-number";

export type DonutSlice = { label: string; weight: number; color: string };

/** A donut chart + side legend for a set of weighted slices (holdings/industry/sector
 * breakdowns) — shared by `etf/holdings-panel.tsx` (TW ETF 成分股/產業分佈) and
 * `us-stock/fund-holdings-panel.tsx` (US ETF 產業分布), which render the exact same shape over
 * different data sources. `weight` is already the percentage value to display (callers scale
 * their own fraction-vs-percent source data before building `slices`, since that scaling
 * differs per source — TW's `current_holdings.weight` is already 0-100, yfinance's
 * `sector_weightings.weight` is a 0-1 fraction). */
export function DonutWithLegend({ slices }: { readonly slices: readonly DonutSlice[] }): ReactNode {
  const locale = useLocale();

  if (slices.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
      <div className="h-[180px] w-[180px] shrink-0">
        <ResponsiveContainer>
          <PieChart>
            <Pie
              data={slices}
              dataKey="weight"
              nameKey="label"
              innerRadius="55%"
              outerRadius="100%"
              strokeWidth={0}
            >
              {slices.map((slice) => (
                <Cell key={slice.label} fill={slice.color} />
              ))}
            </Pie>
            <Tooltip
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              itemStyle={{ color: "var(--text)" }}
              formatter={(value) => [
                `${formatNumber(value as number, locale, { decimals: 2 })}%`,
                "",
              ]}
            />
          </PieChart>
        </ResponsiveContainer>
      </div>
      <div className="flex flex-1 flex-col gap-2">
        {slices.map((slice) => (
          <div key={slice.label} className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-2 text-[13px] text-[var(--text)]">
              <span
                aria-hidden
                className="size-2.5 shrink-0 rounded-full"
                style={{ backgroundColor: slice.color }}
              />
              {slice.label}
            </span>
            <span
              className="shrink-0 font-num text-[13px] tabular-nums"
              style={{ color: slice.color }}
            >
              {formatNumber(slice.weight, locale, { decimals: 2 })}%
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
