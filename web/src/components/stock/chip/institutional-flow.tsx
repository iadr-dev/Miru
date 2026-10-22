"use client";

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
  INSTITUTIONAL_BUCKETS,
  INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS,
  type InstitutionalBucket,
  NetFlowCell,
  TOOLTIP_STYLE,
} from "@/components/stock/chip/shared";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryFailure } from "@/components/ui/query-state";
import { useInstitutionalFlow, usePriceHistory } from "@/hooks/use-stock-data";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import type { Market } from "@/types/settings";

// matches get_institutional_flow's own
// default lookback window (no start_date passed here) so the price-overlay series covers the
// same date range as the flow bars, never a longer/shorter window that would silently misalign.

// Same collapsed-row-count convention every other table in this codebase uses — a handful of
// most-recent days inline, "展開完整" opens the rest in `ExpandModal`.
const INSTITUTIONAL_FLOW_TABLE_COLLAPSED_ROW_COUNT = 6;

/** 進出 chart's click-to-toggle legend (3 buckets + price) — a named module-level component
 * passed to `Legend` as an element (recharts mounts a function `content` as a component type, so
 * an inline closure there remounts the buttons on every toggle and drops keyboard focus). */
function FlowLegend({
  hiddenSeries,
  toggleSeries,
  t,
  tFields,
}: {
  readonly hiddenSeries: ReadonlySet<string>;
  readonly toggleSeries: (series: string) => void;
  readonly t: ReturnType<typeof useTranslations>;
  readonly tFields: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <ul className="mt-2 flex flex-wrap justify-center gap-3">
      {[...INSTITUTIONAL_BUCKETS, "price"].map((series) => {
        const isHidden = hiddenSeries.has(series);
        const color =
          series === "price" ? "var(--chart-9)" : BUCKET_COLORS[series as InstitutionalBucket];
        const label =
          series === "price"
            ? tFields("fields.close")
            : bucketLabel(t, series as InstitutionalBucket);
        return (
          <li key={series}>
            <button
              type="button"
              onClick={() => toggleSeries(series)}
              className="inline-flex items-center gap-1.5 text-[11px]"
              style={{ color: isHidden ? "var(--text-muted)" : "var(--text)" }}
            >
              <span
                aria-hidden
                className="inline-block size-2.5 rounded-sm"
                style={{ backgroundColor: isHidden ? "var(--text-muted)" : color }}
              />
              {label}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

/** 三大法人's 進出 chart — 3 separately-colored bars per day (外資/投信/自營商 net, bucketed from
 * `get_institutional_flow`'s 5 real categories via `bucketedFlowByDate`) with a price-line
 * overlay on a secondary axis, plus a 紅漲綠跌-colored 日期/外資/投信/自營商/合計 table below —
 * matching the reference app's own 進出 view exactly (chart + table, not chart alone). Price
 * comes from `getPriceHistory` (already used in `AssetHeader`), merged by `trade_date` — a day
 * with a flow row but no matching price bar (a gap either series might have) simply renders
 * `price: null`, which recharts skips as a broken line segment rather than a fabricated zero. */
export function InstitutionalFlowBody({
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
  // Clicking a legend entry toggles that series off/on — same interactive-legend pattern
  // `InstitutionalFlowCard` (market/institutional-flow-card.tsx) already uses, applied here to
  // both the 3 bucket bars and the price line.
  const [hiddenSeries, setHiddenSeries] = useState<ReadonlySet<string>>(new Set());
  const toggleSeries = (series: string): void => {
    setHiddenSeries((prev) => {
      const next = new Set(prev);
      if (next.has(series)) {
        next.delete(series);
      } else {
        next.add(series);
      }
      return next;
    });
  };
  const flowQuery = useInstitutionalFlow(symbol, market);
  const priceQuery = usePriceHistory(symbol, market, INSTITUTIONAL_FLOW_PRICE_LOOKBACK_DAYS);

  if (flowQuery.isLoading) return <CardSkeletonBody height={220} />;
  const failed = [flowQuery].find((q) => q.isError);
  if (failed) {
    return <QueryFailure query={failed} />;
  }
  const rows = flowQuery.data?.rows ?? [];
  if (rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>;
  }

  const byDate = bucketedFlowByDate(rows);
  const priceByDate = new Map<string, number>();
  for (const bar of priceQuery.data?.rows ?? []) {
    priceByDate.set(bar.trade_date, bar.close);
  }

  const datesAsc = [...byDate.keys()].sort((a, b) => a.localeCompare(b));
  const points = datesAsc.map((trade_date) => {
    const bucket = byDate.get(trade_date);
    return {
      trade_date,
      price: priceByDate.get(trade_date) ?? null,
      foreign: bucket?.foreign ?? 0,
      trust: bucket?.trust ?? 0,
      dealer: bucket?.dealer ?? 0,
    };
  });
  const tableRowsDesc = [...datesAsc].reverse();

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
              yAxisId="flow"
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              // `flow` bars are the raw-shares bucket sums (`points[bucket]`) — /1000 + 張 suffix
              // inline, matching every other axis-tick formatter in this file that doesn't use
              // i18n for chart-axis labels either.
              tickFormatter={(v: number) =>
                t("lotUnit", { value: formatCompactNumber(v / 1000, locale) })
              }
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
              labelFormatter={String}
            />
            <Legend
              content={
                <FlowLegend
                  hiddenSeries={hiddenSeries}
                  toggleSeries={toggleSeries}
                  t={t}
                  tFields={tFields}
                />
              }
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
                hide={hiddenSeries.has(bucket)}
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
              hide={hiddenSeries.has("price")}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-4">
        <InstitutionalFlowTable
          dates={tableRowsDesc.slice(0, INSTITUTIONAL_FLOW_TABLE_COLLAPSED_ROW_COUNT)}
          byDate={byDate}
          locale={locale}
        />
      </div>

      {tableRowsDesc.length > INSTITUTIONAL_FLOW_TABLE_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)] bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-3)] hover:text-[var(--text)]"
        >
          {t("expandTo", { count: tableRowsDesc.length })}
        </button>
      ) : null}

      <ExpandModal
        open={expanded}
        title={t("institutionalSubTab.flow")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <InstitutionalFlowTable dates={tableRowsDesc} byDate={byDate} locale={locale} />
      </ExpandModal>
    </>
  );
}

/** 進出's 日期/外資/投信/自營商/合計 table — 紅漲綠跌-colored per cell (TW convention: net buy
 * red/▲, net sell green/▼), most-recent-first. Shares `byDate`'s bucket totals with the chart
 * above so the two views can never disagree on a given day's numbers. */
function InstitutionalFlowTable({
  dates,
  byDate,
  locale,
}: {
  readonly dates: readonly string[];
  readonly byDate: ReadonlyMap<string, Record<InstitutionalBucket, number>>;
  readonly locale: string;
}): ReactNode {
  const t = useTranslations("stock.chipAnalysis");
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-[var(--border)] border-b text-[11px] text-[var(--text-muted)]">
            <th className="px-2 py-1.5 text-left font-medium">{t("flowTable.date")}</th>
            {INSTITUTIONAL_BUCKETS.map((bucket) => (
              <th key={bucket} className="px-2 py-1.5 text-right font-medium">
                {bucketLabel(t, bucket)}
              </th>
            ))}
            <th className="px-2 py-1.5 text-right font-medium">{t("flowTable.total")}</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-[var(--border)]">
          {dates.map((date) => {
            const bucket = byDate.get(date) ?? { foreign: 0, trust: 0, dealer: 0 };
            const total = bucket.foreign + bucket.trust + bucket.dealer;
            return (
              <tr key={date}>
                <td className="px-2 py-1.5 font-num text-[var(--text)] tabular-nums">{date}</td>
                {/* `get_institutional_flow`'s `buy`/`sell` (and this bucket sum derived from
                 * them) are raw SHARES — confirmed against TWSE's own T86 三大法人買賣超日報
                 * feed, whose own `hints` field states "單位：股" for these exact buy/sell-shares
                 * columns. Divided to 張 (board lots) here, matching the reference app's own
                 * "-4,460張" display. */}
                <NetFlowCell value={bucket.foreign / 1000} locale={locale} />
                <NetFlowCell value={bucket.trust / 1000} locale={locale} />
                <NetFlowCell value={bucket.dealer / 1000} locale={locale} />
                <NetFlowCell value={total / 1000} locale={locale} />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
