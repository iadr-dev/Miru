"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { getEtfPremiumDiscount } from "@/lib/api/etf-detail";
import type { EtfNavRow } from "@/types/etf-detail";
import type { Market } from "@/types/settings";

const TABLE_ROW_LIMIT = 20;

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

/** 折溢價 tab — three separate cards (chart / chart / data sheet), matching the stock-detail
 * page's own convention (e.g. `research-charts-panel.tsx`'s `ValuationRiverCard` +
 * `basic-info-panel.tsx`'s per-block cards) rather than stacking every chart and the table into
 * one tall card. */
export function PremiumDiscountPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("etf.premiumDiscount");
  const query = useQuery({
    queryKey: ["etf_premium_discount", symbol, market],
    queryFn: () => getEtfPremiumDiscount(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={420} />
      </section>
    );
  }
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

  const latest = data.rows.at(-1);
  const tableRows = data.rows.slice(-TABLE_ROW_LIMIT).reverse();

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <PremiumDiscountBarCard
        rows={data.rows}
        latest={latest}
        currentAum={data.current_aum}
        t={t}
      />
      <PriceNavLineCard rows={data.rows} t={t} />
      <PremiumDiscountTableCard rows={tableRows} t={t} />
    </div>
  );
}

function PremiumDiscountBarCard({
  rows,
  latest,
  currentAum,
  t,
}: {
  readonly rows: readonly EtfNavRow[];
  readonly latest: EtfNavRow | undefined;
  readonly currentAum: number | null | undefined;
  readonly t: (key: string, values?: Record<string, string | number>) => string;
}): ReactNode {
  const locale = useLocale();
  const chartData = rows.map((row) => ({
    date: row.as_of,
    premiumDiscountPct: row.premium_discount_pct,
  }));

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <div className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        {currentAum != null ? (
          <span className="text-[11px] text-[var(--text-muted)]">
            {t("aum", { value: formatCompactNumber(currentAum, locale) })}
          </span>
        ) : null}
      </div>

      {latest?.premium_discount_pct != null ? (
        <div
          className="mb-2 font-num text-[28px] tabular-nums"
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

      <div className="h-[220px] w-full">
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
              contentStyle={TOOLTIP_STYLE}
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
    </section>
  );
}

function PriceNavLineCard({
  rows,
  t,
}: {
  readonly rows: readonly EtfNavRow[];
  readonly t: (key: string) => string;
}): ReactNode {
  const locale = useLocale();
  const chartData = rows.map((row) => ({
    date: row.as_of,
    closePrice: row.close_price,
    estimatedNav: row.estimated_nav,
  }));

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("priceNavTitle")}</h2>
      <div className="h-[220px] w-full">
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
              contentStyle={TOOLTIP_STYLE}
              labelStyle={{ color: "var(--text)", fontWeight: 600 }}
              itemStyle={{ color: "var(--text)" }}
              formatter={(value, name) => [
                formatNumber(value as number, locale, { decimals: 2 }),
                name === "closePrice" ? t("priceSeriesLabel") : t("navSeriesLabel"),
              ]}
            />
            <Legend
              formatter={(value) =>
                value === "closePrice" ? t("priceSeriesLabel") : t("navSeriesLabel")
              }
              wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }}
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
    </section>
  );
}

function PremiumDiscountTableCard({
  rows,
  t,
}: {
  readonly rows: readonly EtfNavRow[];
  readonly t: (key: string) => string;
}): ReactNode {
  const locale = useLocale();

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("dataSheetTitle")}</h2>
      <div className="overflow-x-auto">
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
            {rows.map((row) => (
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
