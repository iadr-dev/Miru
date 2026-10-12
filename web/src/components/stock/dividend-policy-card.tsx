"use client";

import { useQuery } from "@tanstack/react-query";
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
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { RangeSelect } from "@/components/stock/range-select";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getDividendPolicySummary } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type { Market } from "@/types/settings";
import type { DividendEvent } from "@/types/stock-detail";

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

// "計算區間" — how many years of dividend history the chart/table/stats all compute over. Years
// (not months) since the source rows are naturally year-bucketed (`DividendYearSummary`).
const DIVIDEND_POLICY_RANGE_YEARS = { "5y": 5, "10y": 10, all: 30 } as const;
type DividendPolicyRange = keyof typeof DIVIDEND_POLICY_RANGE_YEARS;
const DIVIDEND_POLICY_RANGE_OPTIONS = Object.keys(
  DIVIDEND_POLICY_RANGE_YEARS,
) as DividendPolicyRange[];

// Same collapsed-row-count convention `MATERIAL_ANNOUNCEMENT_FEED_COLLAPSED_ROW_COUNT` uses —
// a handful of most-recent rows inline, "展開完整" opens the rest in `ExpandModal`.
const DIVIDEND_EVENT_COLLAPSED_ROW_COUNT = 6;

function yearsAgoIso(years: number): string {
  const d = new Date();
  d.setFullYear(d.getFullYear() - years);
  return d.toISOString().slice(0, 10);
}

function formatPct(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(2)}%`;
}

/**
 * 股利政策 — replaces the old bare-scatter `DividendResultCard` with the reference design's
 * stats-strip + stacked-by-year chart + 年季 table, all backed by the one composite
 * `get_dividend_policy_summary` call (`backend/src/tools/fundamental_tools.py`) so the headline
 * 現金殖利率/配息率/填息成功率 figures and the chart/table rows can never disagree — they're all
 * derived from the same server-side computation, never re-derived client-side from raw rows.
 */
type DividendSeriesKey = "cash" | "stock";

/** `Legend`'s `formatter` for the 現金/股票股利 click-to-toggle legend — a named function rather
 * than an inline arrow so it isn't redefined on every render of `DividendPolicyCard`. */
function dividendLegendFormatter(
  value: string,
  hiddenSeries: ReadonlySet<DividendSeriesKey>,
  t: ReturnType<typeof useTranslations>,
): ReactNode {
  return (
    <span
      style={{
        cursor: "pointer",
        opacity: hiddenSeries.has(value as DividendSeriesKey) ? 0.4 : 1,
      }}
    >
      {value === "cash" ? t("cashDividendLegend") : t("stockDividendLegend")}
    </span>
  );
}

export function DividendPolicyCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("stock.financials");
  const [range, setRange] = useState<DividendPolicyRange>("10y");
  const [expanded, setExpanded] = useState(false);
  const [hiddenSeries, setHiddenSeries] = useState<ReadonlySet<DividendSeriesKey>>(new Set());
  const startDate = yearsAgoIso(DIVIDEND_POLICY_RANGE_YEARS[range]);
  const query = useQuery({
    queryKey: ["dividend_policy_summary", symbol, market, range],
    queryFn: () => getDividendPolicySummary(symbol, market, { startDate }),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5 xl:col-span-2">
        <CardSkeletonBody height={420} />
      </section>
    );
  }

  const data = query.data;
  const yearSummaries = data?.year_summaries ?? [];
  const events = [...(data?.events ?? [])].sort((a, b) =>
    b.cash_ex_dividend_date && a.cash_ex_dividend_date
      ? b.cash_ex_dividend_date.localeCompare(a.cash_ex_dividend_date)
      : b.announced_date.localeCompare(a.announced_date),
  );
  const stats = data?.stats;

  const chartPoints = yearSummaries.map((y) => ({
    label: String(y.year),
    cash: y.cash_dividend,
    stock: y.stock_dividend,
  }));

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("dividendPolicyTitle")}</h2>
        <div className="flex items-center gap-2">
          <RangeSelect
            value={range}
            onChange={setRange}
            options={DIVIDEND_POLICY_RANGE_OPTIONS}
            labelFor={(key) => t(`dividendPolicyRange.${key}`)}
            ariaLabel={t("dividendPolicyTitle")}
          />
        </div>
      </div>

      {events.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <>
          <DividendPolicyStatsStrip stats={stats} t={t} />

          <div style={{ width: "100%", height: 240 }}>
            <ResponsiveContainer>
              <BarChart data={chartPoints} margin={{ left: 8, right: 8, bottom: 8 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis
                  dataKey="label"
                  tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                  stroke="var(--border)"
                />
                <YAxis
                  tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                  width={40}
                  stroke="var(--border)"
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={{ color: "var(--text)" }}
                  itemStyle={{ color: "var(--text)" }}
                  cursor={{ fill: "var(--accent)", opacity: 0.08 }}
                  formatter={(value) => formatNumber(Number(value), locale, { decimals: 2 })}
                />
                <Legend
                  onClick={(entry) => {
                    const key = entry.dataKey as DividendSeriesKey;
                    setHiddenSeries((prev) => {
                      const next = new Set(prev);
                      if (next.has(key)) {
                        next.delete(key);
                      } else {
                        next.add(key);
                      }
                      return next;
                    });
                  }}
                  formatter={(value) => dividendLegendFormatter(value, hiddenSeries, t)}
                  wrapperStyle={{ fontSize: 12, color: "var(--text-muted)" }}
                />
                <Bar
                  dataKey="cash"
                  stackId="dividend"
                  fill="var(--chart-1)"
                  radius={[4, 4, 0, 0]}
                  hide={hiddenSeries.has("cash")}
                />
                <Bar
                  dataKey="stock"
                  stackId="dividend"
                  fill="var(--chart-3)"
                  radius={[4, 4, 0, 0]}
                  hide={hiddenSeries.has("stock")}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className="mt-4">
            <DividendEventTable
              events={events.slice(0, DIVIDEND_EVENT_COLLAPSED_ROW_COUNT)}
              t={t}
            />
          </div>

          {events.length > DIVIDEND_EVENT_COLLAPSED_ROW_COUNT ? (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className={cn(
                "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
              )}
            >
              <span>{t("expandTo", { count: events.length })}</span>
            </button>
          ) : null}
        </>
      )}

      <ExpandModal
        open={expanded}
        title={t("dividendPolicyTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <DividendEventTable events={events} t={t} />
      </ExpandModal>
    </section>
  );
}

function DividendPolicyStatsStrip({
  stats,
  t,
}: {
  readonly stats:
    | {
        latest_year_total_dividend?: number | null;
        cash_dividend_yield_pct?: number | null;
        payout_ratio_pct?: number | null;
        fill_rate_pct?: number | null;
        fill_rate_sample_size: number;
      }
    | undefined;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const locale = useLocale();
  // Each stat gets its own categorical `--chart-1..6` hue (never `--up`/`--down` — none of these
  // four are a signed price/%/score delta) so the strip reads as 4 distinct at-a-glance figures,
  // matching the reference design's own per-metric coloring.
  const items: { label: string; value: string; color: string }[] = [
    {
      label: t("totalDividendStat"),
      value:
        stats?.latest_year_total_dividend != null
          ? formatNumber(stats.latest_year_total_dividend, locale, { decimals: 2 })
          : "—",
      color: "var(--chart-1)",
    },
    {
      label: t("cashYieldStat"),
      value: formatPct(stats?.cash_dividend_yield_pct),
      color: "var(--chart-3)",
    },
    {
      label: t("payoutRatioStat"),
      value: formatPct(stats?.payout_ratio_pct),
      color: "var(--chart-4)",
    },
    {
      label: t("fillRateStat"),
      value: stats && stats.fill_rate_sample_size > 0 ? formatPct(stats.fill_rate_pct) : "—",
      color: "var(--chart-5)",
    },
  ];

  return (
    <div className="mb-4 grid grid-cols-2 gap-3 rounded-[var(--radius-input)] bg-[var(--surface-2)] p-3 sm:grid-cols-4">
      {items.map((item) => (
        <div key={item.label} className="flex flex-col gap-0.5">
          <span className="font-num text-[16px] tabular-nums" style={{ color: item.color }}>
            {item.value}
          </span>
          <span className="text-[11px] text-[var(--text-muted)]">{item.label}</span>
        </div>
      ))}
    </div>
  );
}

function DividendEventTable({
  events,
  t,
}: {
  readonly events: readonly DividendEvent[];
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("dividendTable.fiscalYear")}</th>
            <th className="px-2 py-1.5 text-left font-medium">{t("dividendTable.exDate")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("dividendTable.cash")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("dividendTable.stock")}</th>
            <th className="px-2 py-1.5 text-right font-medium">{t("dividendTable.total")}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {events.map((event) => {
            const cash = event.cash_dividend ?? 0;
            const stock = event.stock_dividend ?? 0;
            const exDate = event.cash_ex_dividend_date ?? event.stock_ex_dividend_date;
            return (
              <tr key={`${event.fiscal_year}-${event.announced_date}`}>
                <td className="px-2 py-1.5 text-[var(--text)]">{event.fiscal_year}</td>
                <td className="px-2 py-1.5 font-num text-[var(--text-muted)] tabular-nums">
                  {exDate ?? "—"}
                </td>
                <td
                  className="px-2 py-1.5 text-right font-num tabular-nums"
                  style={{ color: "var(--chart-1)" }}
                >
                  {event.cash_dividend != null ? cash.toFixed(2) : "—"}
                </td>
                <td
                  className="px-2 py-1.5 text-right font-num tabular-nums"
                  style={{ color: "var(--chart-3)" }}
                >
                  {event.stock_dividend != null ? stock.toFixed(2) : "—"}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {(cash + stock).toFixed(2)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
