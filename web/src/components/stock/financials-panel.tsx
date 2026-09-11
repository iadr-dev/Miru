"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
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
import { formatCompactNumber, formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { ChartWithDataSheet } from "@/app/[locale]/(app)/chat/cards/chart-with-data-sheet";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { RangeSelect } from "@/components/stock/range-select";
import { getDividendResult, getFinancialStatement, getMonthRevenue } from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";
import { FINANCIAL_STATEMENT_TYPES, type FinancialStatementType } from "@/types/stock-detail";

const TOOLTIP_STYLE = {
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: "var(--radius-input)",
  color: "var(--text)",
};

/** The bar-chart body `MonthRevenueCard` and `DividendResultCard` both wrap in a
 * `ChartWithDataSheet` — identical axis/grid/tooltip shape, differing only in which
 * numeric field to plot, its bar color, and the Y-axis width/tick formatter. Pulled out
 * once so the two cards can't drift on the chart shell as more range toggles get added. */
function SimpleBarChartBody<TPoint extends { label: string }>({
  points,
  dataKey,
  fill,
  yAxisWidth,
  yAxisTickFormatter,
  tooltipFormatter,
}: {
  readonly points: readonly TPoint[];
  readonly dataKey: keyof TPoint & string;
  readonly fill: string;
  readonly yAxisWidth: number;
  readonly yAxisTickFormatter?: (value: number) => string;
  readonly tooltipFormatter: (value: unknown) => string;
}): ReactNode {
  return (
    <ChartWithDataSheet rows={points as Record<string, unknown>[]}>
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <BarChart
            data={points as Record<string, unknown>[]}
            margin={{ left: 8, right: 8, bottom: 8 }}
          >
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="label"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              stroke="var(--border)"
            />
            <YAxis
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              tickFormatter={yAxisTickFormatter}
              width={yAxisWidth}
              stroke="var(--border)"
            />
            <Tooltip contentStyle={TOOLTIP_STYLE} formatter={tooltipFormatter} />
            <Bar dataKey={dataKey as string} fill={fill} radius={4} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </ChartWithDataSheet>
  );
}

/** Neither `get_month_revenue` nor `get_dividend_result` return more than a few recent rows
 * when called with no `start_date` — the router/adapter's own default window is narrower than
 * either range toggle needs, so the 6M/1Y control must pass an explicit cutoff rather than
 * slicing a fixed-size response client-side. */
function monthsAgoIso(months: number): string {
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

/**
 * 財務分析 tab — Phase-1 scope, chart-enriched: 月營收趨勢 (`get_month_revenue`, bar chart citing
 * the pre-computed `trend` MoM/YoY directly, ▲▼-tinted YoY badge on the latest month) 股利政策
 * (`get_dividend_result`, bar chart of settled cash+stock dividend per ex-date), 財務結構/獲利趨勢
 * sub-tabs (損益表/資產負債表/現金流, `get_financial_statement` per `statement_type`, top-N line
 * items as a horizontal bar). Every chart wraps in `ChartWithDataSheet` (the same primitive chat
 * rich-cards use) for a collapsible raw-rows table under the plot — never just a bare table.
 */
export function FinancialsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <MonthRevenueCard symbol={symbol} market={market} />
      <DividendResultCard symbol={symbol} market={market} />
      <FinancialStatementCard symbol={symbol} market={market} />
    </div>
  );
}

const REVENUE_RANGE_MONTHS = { "3m": 3, "6m": 6, "1y": 12 } as const;
type RevenueRange = keyof typeof REVENUE_RANGE_MONTHS;
const REVENUE_RANGE_OPTIONS = Object.keys(REVENUE_RANGE_MONTHS) as RevenueRange[];

function MonthRevenueCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.financials");
  const locale = useLocale();
  const [range, setRange] = useState<RevenueRange>("1y");
  const startDate = monthsAgoIso(REVENUE_RANGE_MONTHS[range]);
  const query = useQuery({
    queryKey: ["month_revenue", symbol, market, range],
    queryFn: () => getMonthRevenue(symbol, market, { startDate }),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={320} />
      </section>
    );
  }
  const trend = query.data?.trend ?? [];
  const latest = trend.at(-1);
  const latestUp = (latest?.yoy_pct ?? 0) >= 0;

  const points = trend.map((point) => ({
    label: `${point.revenue_year}-${String(point.revenue_month).padStart(2, "0")}`,
    revenue: point.revenue,
    yoy_pct: point.yoy_pct,
  }));

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("monthRevenueTitle")}</h2>
        <div className="flex items-center gap-2">
          <RangeSelect
            value={range}
            onChange={setRange}
            options={REVENUE_RANGE_OPTIONS}
            labelFor={(key) => t(`monthRevenueRange.${key}`)}
            ariaLabel={t("monthRevenueTitle")}
          />
          {latest?.yoy_pct != null ? (
            <span
              role="group"
              aria-label={`YoY ${latestUp ? "上漲" : "下跌"} ${Math.abs(latest.yoy_pct).toFixed(1)}%`}
              className="font-num text-[13px] tabular-nums"
              style={{ color: latestUp ? "var(--up)" : "var(--down)" }}
            >
              {latestUp ? "▲" : "▼"} {Math.abs(latest.yoy_pct).toFixed(1)}%
            </span>
          ) : null}
        </div>
      </div>
      {points.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <SimpleBarChartBody
          points={points}
          dataKey="revenue"
          fill="var(--chart-1)"
          yAxisWidth={48}
          yAxisTickFormatter={(v) => formatCompactNumber(v, locale)}
          tooltipFormatter={(value) => formatNumber(Number(value), locale)}
        />
      )}
    </section>
  );
}

const DIVIDEND_RANGE_MONTHS = { "3m": 3, "6m": 6, "1y": 12 } as const;
type DividendRange = keyof typeof DIVIDEND_RANGE_MONTHS;
const DIVIDEND_RANGE_OPTIONS = Object.keys(DIVIDEND_RANGE_MONTHS) as DividendRange[];

function DividendResultCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.financials");
  const locale = useLocale();
  const [range, setRange] = useState<DividendRange>("1y");
  const startDate = monthsAgoIso(DIVIDEND_RANGE_MONTHS[range]);
  const query = useQuery({
    queryKey: ["dividend_result", symbol, market, range],
    queryFn: () => getDividendResult(symbol, market, { startDate }),
  });

  if (query.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={320} />
      </section>
    );
  }
  const rows = query.data?.rows ?? [];
  const points = rows.map((row) => ({
    label: row.trade_date,
    dividend: row.stock_and_cache_dividend,
  }));

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("dividendResultTitle")}</h2>
        <RangeSelect
          value={range}
          onChange={setRange}
          options={DIVIDEND_RANGE_OPTIONS}
          labelFor={(key) => t(`dividendResultRange.${key}`)}
          ariaLabel={t("dividendResultTitle")}
        />
      </div>
      {points.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : (
        <SimpleBarChartBody
          points={points}
          dataKey="dividend"
          fill="var(--chart-2)"
          yAxisWidth={40}
          tooltipFormatter={(value) => formatNumber(Number(value), locale, { decimals: 2 })}
        />
      )}
    </section>
  );
}

function FinancialStatementCard({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.financials");
  const locale = useLocale();
  const [statementType, setStatementType] = useState<FinancialStatementType>("income_statement");
  const query = useQuery({
    queryKey: ["financial_statement", symbol, market, statementType],
    queryFn: () => getFinancialStatement(symbol, market, statementType),
  });

  const lines = [...(query.data?.lines ?? [])].sort((a, b) =>
    b.period_end.localeCompare(a.period_end),
  );
  const latestPeriod = lines[0]?.period_end;
  const latestLines = lines
    .filter((l) => l.period_end === latestPeriod)
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value))
    .slice(0, 10);
  const points = latestLines.map((line) => ({ label: line.origin_name, value: line.value }));

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5 xl:col-span-2">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("statementTitle")}</h2>
        {latestPeriod ? (
          <span className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
            {latestPeriod}
          </span>
        ) : null}
      </div>
      <div role="tablist" aria-label={t("statementTitle")} className="flex flex-wrap gap-1.5">
        {FINANCIAL_STATEMENT_TYPES.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={statementType === key}
            onClick={() => setStatementType(key)}
            className={cn(
              "rounded-[var(--radius-pill)] px-3 py-1.5 text-[12.5px] transition-colors",
              statementType === key
                ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(`statementType.${key}`)}
          </button>
        ))}
      </div>

      <div className="mt-3">
        {query.isLoading ? (
          <CardSkeletonBody height={280} />
        ) : points.length === 0 ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
        ) : (
          <ChartWithDataSheet rows={points}>
            <div style={{ width: "100%", height: Math.max(200, points.length * 32) }}>
              <ResponsiveContainer>
                <BarChart data={points} layout="vertical" margin={{ left: 8, right: 24 }}>
                  <CartesianGrid horizontal={false} stroke="var(--grid)" />
                  <XAxis
                    type="number"
                    tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                    tickFormatter={(v: number) => formatCompactNumber(v, locale)}
                    stroke="var(--border)"
                  />
                  <YAxis
                    type="category"
                    dataKey="label"
                    tick={{ fill: "var(--text-muted)", fontSize: 11 }}
                    width={140}
                    stroke="var(--border)"
                  />
                  <ReferenceLine x={0} stroke="var(--border)" />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    formatter={(value) => formatNumber(Number(value), locale)}
                  />
                  <Bar dataKey="value" radius={4}>
                    {points.map((point) => (
                      <Cell
                        key={point.label}
                        fill={point.value >= 0 ? "var(--up)" : "var(--down)"}
                        aria-label={
                          point.value >= 0
                            ? `${point.label} ▲ ${formatNumber(point.value, locale)}`
                            : `${point.label} ▼ ${formatNumber(point.value, locale)}`
                        }
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartWithDataSheet>
        )}
      </div>
    </section>
  );
}
