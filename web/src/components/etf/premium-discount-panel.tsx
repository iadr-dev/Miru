"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { getEtfPremiumDiscount } from "@/lib/api/etf-detail";
import type { Market } from "@/types/settings";

const TABLE_ROW_LIMIT = 20;

export function PremiumDiscountPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("etf.premiumDiscount");
  const locale = useLocale();
  const query = useQuery({
    queryKey: ["etf_premium_discount", symbol, market],
    queryFn: () => getEtfPremiumDiscount(symbol, market),
  });

  if (query.isLoading) return <CardSkeletonBody height={420} />;
  if (!query.data) return null;

  const data = query.data;

  if (!data.data_available || data.rows.length === 0) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">
          {data.unavailable_reason ?? t("unavailable")}
        </p>
      </section>
    );
  }

  const chartData = data.rows.map((row) => ({
    date: row.as_of,
    premiumDiscountPct: row.premium_discount_pct,
    closePrice: row.close_price,
    estimatedNav: row.estimated_nav,
  }));
  const latest = data.rows.at(-1);
  const tableRows = data.rows.slice(-TABLE_ROW_LIMIT).reverse();

  return (
    <section className="flex flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        {data.current_aum != null ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            {t("aum", { value: formatNumber(data.current_aum, locale, { decimals: 0 }) })}
          </span>
        ) : null}
      </div>

      {latest?.premium_discount_pct != null ? (
        <div
          className="my-2 font-num text-[28px] tabular-nums"
          style={{
            color:
              latest.premium_discount_pct > 0
                ? "var(--up)"
                : latest.premium_discount_pct < 0
                  ? "var(--down)"
                  : "var(--text)",
          }}
        >
          {latest.premium_discount_pct > 0 ? "▲" : latest.premium_discount_pct < 0 ? "▼" : ""}
          {formatNumber(Math.abs(latest.premium_discount_pct), locale, { decimals: 2 })}%
        </div>
      ) : null}

      <div className="h-[160px] w-full">
        <ResponsiveContainer>
          <ComposedChart data={chartData} margin={{ left: 8, right: 8 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              stroke="var(--border)"
            />
            <YAxis
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              width={40}
              stroke="var(--border)"
            />
            <Tooltip
              cursor={{ fill: "var(--text)", opacity: 0.08 }}
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              labelStyle={{ color: "var(--text)", fontWeight: 600 }}
              itemStyle={{ color: "var(--text)" }}
              formatter={(value) => [
                `${formatNumber(value as number, locale, { decimals: 2 })}%`,
                t("chartSeriesLabel"),
              ]}
            />
            <Bar dataKey="premiumDiscountPct" radius={2}>
              {chartData.map((point) => (
                <Cell
                  key={point.date}
                  fill={
                    point.premiumDiscountPct != null && point.premiumDiscountPct > 0
                      ? "var(--up)"
                      : "var(--down)"
                  }
                />
              ))}
            </Bar>
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-3 h-[160px] w-full">
        <ResponsiveContainer>
          <ComposedChart data={chartData} margin={{ left: 8, right: 8 }}>
            <CartesianGrid stroke="var(--grid)" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              stroke="var(--border)"
            />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              width={40}
              stroke="var(--border)"
            />
            <Tooltip
              contentStyle={{
                background: "var(--surface-2)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-input)",
                color: "var(--text)",
              }}
              labelStyle={{ color: "var(--text)", fontWeight: 600 }}
              itemStyle={{ color: "var(--text)" }}
              formatter={(value, name) => [
                formatNumber(value as number, locale, { decimals: 2 }),
                name === "closePrice" ? t("priceSeriesLabel") : t("navSeriesLabel"),
              ]}
            />
            <Line
              type="monotone"
              dataKey="closePrice"
              name="closePrice"
              stroke="var(--chart-1)"
              strokeWidth={1.5}
              dot={false}
              connectNulls
            />
            <Line
              type="monotone"
              dataKey="estimatedNav"
              name="estimatedNav"
              stroke="var(--chart-2)"
              strokeWidth={1.5}
              dot={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4 overflow-x-auto border-[var(--border)] border-t pt-3">
        <table className="w-full text-[12.5px]">
          <thead>
            <tr className="border-[var(--border)] border-b text-[var(--text-muted)]">
              <th className="py-2 text-left font-medium">{t("column.date")}</th>
              <th className="py-2 text-right font-medium">{t("column.price")}</th>
              <th className="py-2 text-right font-medium">{t("column.nav")}</th>
              <th className="py-2 text-right font-medium">{t("column.premiumDiscount")}</th>
            </tr>
          </thead>
          <tbody>
            {tableRows.map((row) => (
              <tr key={row.as_of} className="border-[var(--border)] border-b">
                <td className="py-2 text-[var(--text)]">{row.as_of}</td>
                <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(row.close_price ?? null, locale, { decimals: 2 })}
                </td>
                <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
                  {formatNumber(row.estimated_nav ?? null, locale, { decimals: 2 })}
                </td>
                <td
                  className="py-2 text-right font-num tabular-nums"
                  style={{
                    color:
                      row.premium_discount_pct != null && row.premium_discount_pct > 0
                        ? "var(--up)"
                        : "var(--down)",
                  }}
                >
                  {row.premium_discount_pct != null
                    ? `${formatNumber(row.premium_discount_pct, locale, { decimals: 2 })}%`
                    : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
