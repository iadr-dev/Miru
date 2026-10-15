"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
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
  INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS,
  type InstitutionalBucket,
  TOOLTIP_STYLE,
} from "@/components/stock/chip/shared";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryFailure } from "@/components/ui/query-state";
import { useInstitutionalFlow, usePriceHistory } from "@/hooks/use-stock-data";
import { queryKeys } from "@/lib/api/query-keys";
import { getForeignShareholding } from "@/lib/api/stock-detail";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import type { Market } from "@/types/settings";

const HOLDINGS_TABLE_COLLAPSED_ROW_COUNT = 6;

/** 三大法人's 持股 sub-tab — 外資's line is REAL holding shares (`get_foreign_shareholding`'s own
 * `foreign_investment_shares`, the only per-stock holding-BALANCE dataset any configured source
 * exposes — confirmed live: neither FinMind, TWSE OpenAPI, nor TPEx OpenAPI publishes a 投信/
 * 自營商 per-stock holding-balance equivalent; Taiwan's disclosure regime mandates a per-stock
 * foreign-holding-ratio filing with no domestic-institutional counterpart). 投信/自營商 have no
 * such filing, so their lines are an ESTIMATE — cumulative net buy/sell from
 * `get_institutional_flow`, rebased to 0 at the window's first date (a running total of real
 * daily flow, not a fabricated absolute holding count) — explicitly labeled as such via
 * `estimateCaveat` (a permanent, non-removable caveat here, same non-negotiable-caveat rule
 * web/OVERVIEW.md §4 applies everywhere else) rather than presented as equally real to 外資's line.
 * Table below mirrors the chart 1:1 — 外資's real 張數+% columns, 投信/自營商's estimated
 * cumulative-net columns, both sharing this component's own merge so chart and table can't
 * diverge. */
const HOLDINGS_SERIES = ["foreign_shares", "trust_estimate", "dealer_estimate"] as const;

type HoldingsSeries = (typeof HOLDINGS_SERIES)[number];

const HOLDINGS_SERIES_BUCKET: Record<HoldingsSeries, InstitutionalBucket> = {
  foreign_shares: "foreign",
  trust_estimate: "trust",
  dealer_estimate: "dealer",
};

function isHoldingsSeries(value: unknown): value is HoldingsSeries {
  return HOLDINGS_SERIES.some((series) => series === value);
}

/** Legend/tooltip label for a 持股 chart series: 收盤價, 外資, or 投信/自營商 (估算). */
function holdingsSeriesLabel(
  name: string,
  t: ReturnType<typeof useTranslations>,
  tFields: ReturnType<typeof useTranslations>,
): string {
  if (name === "price") return tFields("fields.close");
  if (!isHoldingsSeries(name)) return name;
  const bucket = HOLDINGS_SERIES_BUCKET[name];
  return name === "foreign_shares"
    ? bucketLabel(t, bucket)
    : t("estimateLegend", { label: bucketLabel(t, bucket) });
}

export function ForeignShareholdingBody({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  const tFields = useTranslations("chat.cards");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  // 外資 holds ~100x what the 投信/自營商 estimates do, so on one axis their bars vanish;
  // clicking a legend item hides that series and the axis rescales to what is left.
  const [hiddenSeries, setHiddenSeries] = useState<ReadonlySet<HoldingsSeries>>(new Set());
  const toggleSeries = (series: HoldingsSeries): void => {
    setHiddenSeries((prev) => {
      const next = new Set(prev);
      if (next.has(series)) next.delete(series);
      else next.add(series);
      return next;
    });
  };
  const seriesLabel = (name: string): string => holdingsSeriesLabel(name, t, tFields);
  const shareholdingQuery = useQuery({
    queryKey: queryKeys.symbolRead("stock_foreign_shareholding", symbol, market),
    queryFn: () => getForeignShareholding(symbol, market),
  });
  const flowQuery = useInstitutionalFlow(symbol, market);
  const priceQuery = usePriceHistory(symbol, market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS);

  if (shareholdingQuery.isLoading || flowQuery.isLoading) return <CardSkeletonBody height={280} />;
  const failed = [shareholdingQuery, flowQuery].find((q) => q.isError);
  if (failed) {
    return <QueryFailure query={failed} />;
  }
  const shareholdingRows = [...(shareholdingQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const flowRows = flowQuery.data?.rows ?? [];
  if (shareholdingRows.length === 0 && flowRows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const foreignByDate = new Map<string, { shares: number; ratio: number }>();
  for (const row of shareholdingRows) {
    foreignByDate.set(row.trade_date, {
      shares: row.foreign_investment_shares,
      ratio: row.foreign_investment_shares_ratio,
    });
  }
  // `number_of_shares_issued` (total shares outstanding) rides along on every
  // `get_foreign_shareholding` row — reused here as the denominator for 投信/自營商's estimated
  // %-of-shares-outstanding, the same real figure `foreign_investment_shares_ratio` itself is
  // computed from upstream, just applied to the cumulative-flow estimate instead of a real
  // holding count.
  const sharesIssued = shareholdingRows.at(-1)?.number_of_shares_issued;
  const flowByDate = bucketedFlowByDate(flowRows);
  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }

  const dates = [...new Set([...foreignByDate.keys(), ...flowByDate.keys()])].sort((a, b) =>
    a.localeCompare(b),
  );
  let trustCumulative = 0;
  let dealerCumulative = 0;
  const trustEstimateByDate = new Map<string, number>();
  const dealerEstimateByDate = new Map<string, number>();
  for (const date of dates) {
    const bucket = flowByDate.get(date);
    trustCumulative += bucket?.trust ?? 0;
    dealerCumulative += bucket?.dealer ?? 0;
    trustEstimateByDate.set(date, trustCumulative);
    dealerEstimateByDate.set(date, dealerCumulative);
  }

  // `foreign_investment_shares` (confirmed 股 — TWSE's own MI_QFIIS feed labels this exact
  // column "單位:股") and the 投信/自營商 cumulative-net estimate (raw shares off
  // `bucketedFlowByDate`, same TWSE T86-confirmed unit as everywhere else in this panel) are both
  // converted to 張 here, once, for the chart's own series.
  const points = dates.map((trade_date) => {
    const foreignShares = foreignByDate.get(trade_date)?.shares;
    const trustEstimate = trustEstimateByDate.get(trade_date);
    const dealerEstimate = dealerEstimateByDate.get(trade_date);
    return {
      trade_date,
      foreign_shares: foreignShares != null ? foreignShares / 1000 : null,
      trust_estimate: trustEstimate != null ? trustEstimate / 1000 : null,
      dealer_estimate: dealerEstimate != null ? dealerEstimate / 1000 : null,
      price: priceByDate.get(trade_date) ?? null,
    };
  });
  const tableDatesDesc = [...dates].reverse();

  return (
    <>
      <div style={{ width: "100%", height: 240 }}>
        <ResponsiveContainer>
          <ComposedChart data={points} margin={{ left: 8, right: 8, bottom: 8 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis
              dataKey="trade_date"
              tick={{ fill: "var(--text-muted)", fontSize: 10 }}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="holdings"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              // `holdings` series (`foreign_shares`/`trust_estimate`/`dealer_estimate`) are
              // already converted to 張 in `points` above — inline suffix only.
              tickFormatter={(v: number) => `${formatCompactNumber(v, locale)}張`}
              width={48}
              stroke="var(--border)"
            />
            <YAxis
              yAxisId="price"
              orientation="right"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              tickFormatter={(v: number) => formatNumber(v, locale)}
              width={48}
              stroke="var(--border)"
            />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelStyle={{ color: "var(--text)" }}
              itemStyle={{ color: "var(--text)" }}
              cursor={{ fill: "var(--accent)", opacity: 0.08 }}
              formatter={(value, name) => [
                name === "price"
                  ? formatNumber(Number(value), locale)
                  : t("lotUnit", { value: formatNumber(Number(value), locale) }),
                seriesLabel(String(name)),
              ]}
            />
            <Legend
              onClick={(entry) => {
                const key = entry.dataKey;
                if (isHoldingsSeries(key)) toggleSeries(key);
              }}
              formatter={(value) => (
                <span
                  style={{
                    cursor: value === "price" ? "default" : "pointer",
                    opacity: isHoldingsSeries(value) && hiddenSeries.has(value) ? 0.4 : 1,
                  }}
                >
                  {seriesLabel(String(value))}
                </span>
              )}
              wrapperStyle={{ fontSize: 11, color: "var(--text-muted)" }}
            />
            <ReferenceLine yAxisId="holdings" y={0} stroke="var(--border-strong)" />
            {HOLDINGS_SERIES.map((series) => (
              <Bar
                key={series}
                yAxisId="holdings"
                dataKey={series}
                name={series}
                fill={BUCKET_COLORS[HOLDINGS_SERIES_BUCKET[series]]}
                // The 投信/自營商 values are estimates from cumulative flow, not reported holdings;
                // a lighter fill keeps that distinction the dashed lines used to carry.
                fillOpacity={series === "foreign_shares" ? 1 : 0.6}
                radius={[2, 2, 0, 0]}
                maxBarSize={14}
                hide={hiddenSeries.has(series)}
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

      <div className="mt-4">
        <HoldingsTable
          dates={tableDatesDesc.slice(0, HOLDINGS_TABLE_COLLAPSED_ROW_COUNT)}
          foreignByDate={foreignByDate}
          trustEstimateByDate={trustEstimateByDate}
          dealerEstimateByDate={dealerEstimateByDate}
          sharesIssued={sharesIssued}
          locale={locale}
        />
      </div>

      {tableDatesDesc.length > HOLDINGS_TABLE_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: tableDatesDesc.length })}
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={t("institutionalSubTab.holdings")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <HoldingsTable
          dates={tableDatesDesc}
          foreignByDate={foreignByDate}
          trustEstimateByDate={trustEstimateByDate}
          dealerEstimateByDate={dealerEstimateByDate}
          sharesIssued={sharesIssued}
          locale={locale}
        />
      </ExpandModal>
    </>
  );
}

/** `cumulativeNet` is the raw-shares 投信/自營商 cumulative-net estimate (`bucketedFlowByDate`'s
 * bucket sums, same TWSE T86-confirmed shares unit as everywhere else in this panel) — converted
 * to 張 here before formatting; the %-of-shares-outstanding denominator (`sharesIssued`, itself a
 * real share count) is unaffected by the unit change since both sides of that ratio must stay in
 * the same unit (shares ÷ shares), so it's applied to `cumulativeNet` before the /1000 lot
 * conversion, not after. */
function estimatedSharesCell(
  cumulativeNet: number | undefined,
  sharesIssued: number | undefined,
  locale: string,
  lotUnit: (value: string) => string,
): string {
  if (cumulativeNet == null) return "—";
  const lotsText = lotUnit(formatNumber(cumulativeNet / 1000, locale));
  if (!sharesIssued || sharesIssued <= 0) return lotsText;
  return `${lotsText} (${((cumulativeNet / sharesIssued) * 100).toFixed(2)}%)`;
}

function HoldingsTable({
  dates,
  foreignByDate,
  trustEstimateByDate,
  dealerEstimateByDate,
  sharesIssued,
  locale,
}: {
  readonly dates: readonly string[];
  readonly foreignByDate: ReadonlyMap<string, { shares: number; ratio: number }>;
  readonly trustEstimateByDate: ReadonlyMap<string, number>;
  readonly dealerEstimateByDate: ReadonlyMap<string, number>;
  readonly sharesIssued: number | undefined;
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("flowTable.date")}</th>
            <th
              className="px-2 py-1.5 text-right font-medium"
              style={{ color: BUCKET_COLORS.foreign }}
            >
              {bucketLabel(t, "foreign")}
            </th>
            <th
              className="px-2 py-1.5 text-right font-medium"
              style={{ color: BUCKET_COLORS.trust }}
            >
              {t("estimateLegend", { label: bucketLabel(t, "trust") })}
            </th>
            <th
              className="px-2 py-1.5 text-right font-medium"
              style={{ color: BUCKET_COLORS.dealer }}
            >
              {t("estimateLegend", { label: bucketLabel(t, "dealer") })}
            </th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {dates.map((date) => {
            const foreign = foreignByDate.get(date);
            return (
              <tr key={date}>
                <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{date}</td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text)] tabular-nums">
                  {/* `foreign.shares` is `foreign_investment_shares` — confirmed 股 against
                   * TWSE's own MI_QFIIS feed ("單位:股") — /1000 to 張 before formatting. */}
                  {foreign
                    ? `${t("lotUnit", { value: formatNumber(foreign.shares / 1000, locale) })} (${foreign.ratio.toFixed(2)}%)`
                    : "—"}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text-muted)] tabular-nums">
                  {estimatedSharesCell(trustEstimateByDate.get(date), sharesIssued, locale, (v) =>
                    t("lotUnit", { value: v }),
                  )}
                </td>
                <td className="px-2 py-1.5 text-right font-num text-[var(--text-muted)] tabular-nums">
                  {estimatedSharesCell(dealerEstimateByDate.get(date), sharesIssued, locale, (v) =>
                    t("lotUnit", { value: v }),
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
