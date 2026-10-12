"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import {
  BUCKET_COLORS,
  bucketedFlowByDate,
  bucketLabel,
  formatSignedNumber,
  INSTITUTIONAL_BUCKETS,
  type InstitutionalBucket,
  isoDaysAgo,
  SnapshotStat,
  SubTabRow,
  TOOLTIP_STYLE,
} from "@/components/stock/chip/shared";
import { getPriceHistory } from "@/lib/api/market";
import { getForeignShareholding, getStockInstitutionalFlow } from "@/lib/api/stock-detail";
import { FLOW_CHART_TRADING_DAYS, groupFlowByPeriod } from "@/lib/flow-periods";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import type { Market } from "@/types/settings";

const SUMMARY_PERIODS = ["day", "week", "month"] as const;

type SummaryPeriod = (typeof SUMMARY_PERIODS)[number];

// Trading-day counts each period's mini-chart/stat-block looks back over — matches the
// reference app's own 日/週/月 tabs (one day, one calendar week ≈5 trading days, one calendar
// month ≈20 trading days) rather than a literal single day/week/month window, since the flow
// data this reads is trading-day-indexed, not calendar-indexed.
const SUMMARY_PERIOD_TRADING_DAYS: Record<SummaryPeriod, number> = { day: 1, week: 5, month: 20 };

// The 摘要 chart's window: ~3 months, read with calendar-day slack for holidays. Price history is
// capped at 60 trading days by the backend, so the close line covers the latest ~3 months.
const FLOW_CHART_CALENDAR_DAYS = 100;

const FLOW_CHART_PRICE_DAYS = 60;

/** The default and the 3-month reads overlap; keep one row per (date, investor). */
function uniqueFlowRows<T extends { trade_date: string; investor: string }>(
  rows: readonly T[],
): T[] {
  const seen = new Map<string, T>();
  for (const row of rows) seen.set(`${row.trade_date}|${row.investor}`, row);
  return [...seen.values()];
}

/** 三大法人's 摘要 sub-tab — a top totals row (三大法人 combined + 外資/投信/自營商, each 累計
 * 買賣超 + estimated %-of-shares-outstanding, each card tinted by its own bucket color) plus
 * 日/週/月 tabs, each showing that period's net-flow-per-bucket stat block and a mini combo
 * chart (bucket net-flow bars + price line) over the period's trading-day window. 外資's %
 * comes from `get_foreign_shareholding`'s real ratio; 投信/自營商 have no per-stock
 * holding-balance disclosure anywhere in this codebase's configured sources (confirmed:
 * neither FinMind, TWSE OpenAPI, nor TPEx OpenAPI publishes one — see `ForeignShareholdingBody`'s
 * own docstring for the full investigation), so their %s are the same cumulative-net-flow
 * estimate that component uses. */
export function InstitutionalTotalsBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const tFields = useTranslations("chat.cards");
  const locale = useLocale();
  const [period, setPeriod] = useState<SummaryPeriod>("day");
  const [hiddenBuckets, setHiddenBuckets] = useState<ReadonlySet<InstitutionalBucket>>(new Set());
  const toggleBucket = (bucket: InstitutionalBucket): void => {
    setHiddenBuckets((prev) => {
      const next = new Set(prev);
      if (next.has(bucket)) {
        next.delete(bucket);
      } else {
        next.add(bucket);
      }
      return next;
    });
  };
  const flowQuery = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market],
    queryFn: () => getStockInstitutionalFlow(symbol, market),
  });
  const shareholdingQuery = useQuery({
    queryKey: ["stock_foreign_shareholding", symbol, market],
    queryFn: () => getForeignShareholding(symbol, market),
  });
  // The chart spans ~3 months whatever the period; 日/週/月 only sets the bar size. The default
  // read above is about a month, so ask for 3 months too and merge (a throttled upstream that
  // returns nothing for the longer range still leaves the month on screen).
  const chartStart = useMemo(() => isoDaysAgo(FLOW_CHART_CALENDAR_DAYS), []);
  const longFlowQuery = useQuery({
    queryKey: ["stock_institutional_flow", symbol, market, chartStart],
    queryFn: () => getStockInstitutionalFlow(symbol, market, { startDate: chartStart }),
  });
  const priceQuery = useQuery({
    queryKey: ["price_history", symbol, market, FLOW_CHART_PRICE_DAYS],
    queryFn: () => getPriceHistory([symbol], market, FLOW_CHART_PRICE_DAYS),
  });

  if (flowQuery.isLoading || shareholdingQuery.isLoading) return <CardSkeletonBody height={320} />;
  const flowRows = flowQuery.data?.rows ?? [];
  if (flowRows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const byDate = bucketedFlowByDate(flowRows);
  const datesAsc = [...byDate.keys()].sort((a, b) => a.localeCompare(b));
  const cumulativeTotals = datesAsc.reduce(
    (acc, date) => {
      const bucket = byDate.get(date);
      if (bucket) {
        acc.foreign += bucket.foreign;
        acc.trust += bucket.trust;
        acc.dealer += bucket.dealer;
      }
      return acc;
    },
    { foreign: 0, trust: 0, dealer: 0 },
  );
  const combinedTotal = cumulativeTotals.foreign + cumulativeTotals.trust + cumulativeTotals.dealer;

  const shareholdingRows = [...(shareholdingQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const latestShareholding = shareholdingRows.at(-1);
  const sharesIssued = latestShareholding?.number_of_shares_issued;
  const bucketPct = (shares: number): number | null =>
    sharesIssued && sharesIssued > 0 ? (shares / sharesIssued) * 100 : null;

  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }
  const windowDates = datesAsc.slice(-SUMMARY_PERIOD_TRADING_DAYS[period]);
  const periodTotals = windowDates.reduce(
    (acc, date) => {
      const bucket = byDate.get(date);
      if (bucket) {
        acc.foreign += bucket.foreign;
        acc.trust += bucket.trust;
        acc.dealer += bucket.dealer;
      }
      return acc;
    },
    { foreign: 0, trust: 0, dealer: 0 },
  );
  const chartByDate = bucketedFlowByDate(
    uniqueFlowRows([...(longFlowQuery.data?.rows ?? []), ...flowRows]),
  );
  const chartDates = [...chartByDate.keys()]
    .sort((a, b) => a.localeCompare(b))
    .slice(-FLOW_CHART_TRADING_DAYS);
  const chartBars = groupFlowByPeriod(
    chartDates.map((trade_date) => {
      const bucket = chartByDate.get(trade_date);
      return {
        trade_date,
        foreign: bucket?.foreign ?? 0,
        trust: bucket?.trust ?? 0,
        dealer: bucket?.dealer ?? 0,
        price: priceByDate.get(trade_date) ?? null,
      };
    }),
    period,
  );

  const totalsCards: {
    key: string;
    label: string;
    shares: number;
    pct: number | null;
    color: string;
  }[] = [
    {
      key: "combined",
      label: t("institutionalCombined"),
      shares: combinedTotal,
      pct: bucketPct(combinedTotal),
      color: "var(--accent)",
    },
    {
      key: "foreign",
      label: bucketLabel(t, "foreign"),
      shares: cumulativeTotals.foreign,
      pct:
        latestShareholding?.foreign_investment_shares_ratio ?? bucketPct(cumulativeTotals.foreign),
      color: BUCKET_COLORS.foreign,
    },
    {
      key: "trust",
      label: bucketLabel(t, "trust"),
      shares: cumulativeTotals.trust,
      pct: bucketPct(cumulativeTotals.trust),
      color: BUCKET_COLORS.trust,
    },
    {
      key: "dealer",
      label: bucketLabel(t, "dealer"),
      shares: cumulativeTotals.dealer,
      pct: bucketPct(cumulativeTotals.dealer),
      color: BUCKET_COLORS.dealer,
    },
  ];

  return (
    <div>
      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {totalsCards.map((card) => {
          // "combined" has no matching bucket bar in the chart below (it's the sum of all 3),
          // so it's rendered as plain, non-interactive stat card — only the 3 per-bucket cards
          // toggle a chart series, same click-to-hide pattern the 進出 chart's own `<Legend>` uses.
          const bucket = INSTITUTIONAL_BUCKETS.find((b) => b === card.key);
          const isHidden = bucket ? hiddenBuckets.has(bucket) : false;
          const content = (
            <>
              <p className="text-[11px] text-[var(--text-muted)]">{card.label}</p>
              <p
                className="font-num text-[14px] tabular-nums"
                style={{ color: isHidden ? "var(--text-muted)" : card.color }}
              >
                {/* `card.shares` is `bucketedFlowByDate`'s cumulative net (raw SHARES off
                 * `get_institutional_flow`'s own `buy`/`sell`, see `NetFlowCell`'s own docstring
                 * for the TWSE T86 confirmation) — divide to 張 before formatting, same as every
                 * other institutional-flow number in this card. */}
                {t("lotUnit", { value: formatCompactNumber(card.shares / 1000, locale) })}
              </p>
              <p className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
                {card.pct != null ? `佔${card.pct.toFixed(2)}%` : "—"}
              </p>
            </>
          );
          const style = {
            borderColor: `color-mix(in srgb, ${card.color} ${isHidden ? 15 : 35}%, transparent)`,
            backgroundColor: `color-mix(in srgb, ${card.color} ${isHidden ? 5 : 14}%, transparent)`,
          };
          if (!bucket) {
            return (
              <div
                key={card.key}
                className="rounded-[var(--radius-input)] border p-2.5 backdrop-blur-md"
                style={style}
              >
                {content}
              </div>
            );
          }
          return (
            <button
              key={card.key}
              type="button"
              onClick={() => toggleBucket(bucket)}
              aria-pressed={!isHidden}
              className="rounded-[var(--radius-input)] border p-2.5 text-left backdrop-blur-md transition-opacity"
              style={style}
            >
              {content}
            </button>
          );
        })}
      </div>

      <SubTabRow
        tabs={SUMMARY_PERIODS}
        active={period}
        onChange={setPeriod}
        labelFor={(p) => t(`summaryPeriod.${p}`)}
      />
      <div className="mb-3 grid grid-cols-3 gap-2 font-num text-[12px] tabular-nums">
        {INSTITUTIONAL_BUCKETS.map((bucket) => (
          <SnapshotStat
            key={bucket}
            label={bucketLabel(t, bucket)}
            // `periodTotals[bucket]` is a raw-shares sum off `get_institutional_flow` (same as
            // `card.shares` above) — divide to 張 before formatting.
            value={t("lotUnit", { value: formatSignedNumber(periodTotals[bucket] / 1000, locale) })}
            tone={periodTotals[bucket] >= 0 ? "up" : "down"}
          />
        ))}
      </div>
      <div style={{ width: "100%", height: 160 }}>
        <ResponsiveContainer>
          <ComposedChart data={chartBars} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="label"
              tick={{ fill: "var(--text-muted)", fontSize: 9 }}
              tickFormatter={(label: string) => (period === "month" ? label : label.slice(5))}
              minTickGap={16}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="flow"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              // Same raw-shares bucket sums as the 進出 chart above — /1000 + inline 張 suffix.
              tickFormatter={(v: number) => `${formatCompactNumber(v / 1000, locale)}張`}
              width={40}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="price"
              orientation="right"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              tickFormatter={(v: number) => formatNumber(v, locale)}
              width={40}
              stroke="var(--border)"
            />
            <ReferenceLine yAxisId="flow" y={0} stroke="var(--border)" />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelStyle={{ color: "var(--text)" }}
              itemStyle={{ color: "var(--text)" }}
              cursor={{ fill: "var(--accent)", opacity: 0.08 }}
              formatter={(value, name) => [
                name === "price"
                  ? formatNumber(Number(value), locale)
                  : t("lotUnit", { value: formatNumber(Number(value) / 1000, locale) }),
                name === "price"
                  ? tFields("fields.close")
                  : bucketLabel(t, name as InstitutionalBucket),
              ]}
            />
            {INSTITUTIONAL_BUCKETS.map((bucket) => (
              <Bar
                key={bucket}
                yAxisId="flow"
                dataKey={bucket}
                name={bucket}
                stackId="flow"
                fill={BUCKET_COLORS[bucket]}
                radius={2}
                hide={hiddenBuckets.has(bucket)}
              />
            ))}
            <Line
              yAxisId="price"
              type="monotone"
              dataKey="price"
              name="price"
              stroke="var(--chart-9)"
              strokeWidth={2}
              dot={false}
              connectNulls
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
