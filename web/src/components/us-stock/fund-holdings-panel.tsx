"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SymbolLink } from "@/components/market/symbol-link";
import { type DonutSlice, DonutWithLegend } from "@/components/ui/donut-with-legend";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryFailure } from "@/components/ui/query-state";
import { getFundHoldings } from "@/lib/api/etf-detail";
import { queryKeys } from "@/lib/api/query-keys";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type { FundHoldingRow, FundSectorWeightingRow } from "@/types/etf-detail";
import type { Market } from "@/types/settings";

const DONUT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
] as const;
const OTHERS_COLOR = "var(--text-muted)";
const TOP_N = 4;
const DATA_SHEET_COLLAPSED_ROW_COUNT = 10;

/** 持股與產業 tab — the US-ETF counterpart to the TW ETF's 成分股/產業分佈 sub-tabs, but backed
 * by `get_fund_holdings` (yfinance's own `FundsData.top_holdings`/`sector_weightings`) rather
 * than TDCC/PCF data, since that source is TW-only. Genuinely different data shape too: TW's
 * `current_holdings` carries every constituent at its real weight; yfinance's fund holdings are
 * already a "top N" slice (typically ~10 rows) with no "the rest" total, so there is no
 * expand-to-full-constituent-list affordance here — the data sheet just shows what the source
 * gives, no synthesized "and N more" row. Shares `DonutWithLegend` with `etf/holdings-panel.tsx`
 * for the sector-breakdown donut — same shape, different source data. */
export function FundHoldingsPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.fundHoldings");
  const query = useQuery({
    queryKey: queryKeys.symbolRead("fund_holdings", symbol, market),
    queryFn: () => getFundHoldings(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <CardSkeletonBody height={360} />
      </section>
    );
  }
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const data = query.data;
  if (!data) return null;

  if (data.holdings.length === 0 && data.sector_weightings.length === 0) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)] leading-[1.6]">
          {t("unavailable")}
        </p>
      </section>
    );
  }

  const sectorSlices = topSectorSlices(data.sector_weightings, t("others"));

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      {sectorSlices.length > 0 ? (
        <section className="surface-card p-4 sm:p-5 xl:col-span-2">
          <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("sectorTitle")}</h2>
          <DonutWithLegend slices={sectorSlices} />
        </section>
      ) : null}
      {data.holdings.length > 0 ? (
        <HoldingsCard symbol={symbol} market={market} holdings={data.holdings} />
      ) : null}
    </div>
  );
}

function HoldingsCard({
  symbol,
  market,
  holdings,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly holdings: readonly FundHoldingRow[];
}): ReactNode {
  const t = useTranslations("usStock.fundHoldings");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);
  const ranked = [...holdings].sort((a, b) => b.weight - a.weight);
  const collapsedRows = ranked.slice(0, DATA_SHEET_COLLAPSED_ROW_COUNT);

  const table = (rows: readonly FundHoldingRow[]): ReactNode => (
    <table className="w-full text-[12.5px]">
      <thead>
        <tr className="border-[var(--border)] border-b text-[var(--text-muted)]">
          <th className="py-2 text-left font-medium">{t("column.rank")}</th>
          <th className="py-2 text-left font-medium">{t("column.holding")}</th>
          <th className="py-2 text-right font-medium">{t("column.weight")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={row.holding_symbol} className="border-[var(--border)] border-b">
            <td className="py-2 text-[var(--text-muted)] tabular-nums">{index + 1}</td>
            <td className="py-2 text-[var(--text)]">
              <SymbolLink symbol={row.holding_symbol} market={market} className="hover:underline">
                {row.holding_name || row.holding_symbol}
              </SymbolLink>
              {row.holding_symbol === symbol ? null : (
                <span className="ml-1 font-num text-[11px] text-[var(--text-muted)]">
                  {row.holding_symbol}
                </span>
              )}
            </td>
            <td className="py-2 text-right font-num text-[var(--text)] tabular-nums">
              {formatNumber(row.weight * 100, locale, { decimals: 2 })}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );

  return (
    <section className="surface-card p-4 sm:p-5 xl:col-span-2">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("holdingsTitle")}</h2>
      <div className="overflow-x-auto">{table(collapsedRows)}</div>
      {ranked.length > DATA_SHEET_COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={cn(
            "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          {t("expandTo", { count: ranked.length })}
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={t("holdingsTitle")}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        {table(ranked)}
      </ExpandModal>
    </section>
  );
}

/** `FundSectorWeightingRow.weight` is a 0-1 fraction (yfinance's own convention) — scaled to a
 * 0-100 percent here so it matches `DonutWithLegend`'s "already a percent" contract, the same
 * convention `etf/holdings-panel.tsx`'s TW-sourced slices (already 0-100) satisfy natively. */
function topSectorSlices(
  rows: readonly FundSectorWeightingRow[],
  othersLabel: string,
): DonutSlice[] {
  const sorted = [...rows].sort((a, b) => b.weight - a.weight);
  const top = sorted.slice(0, TOP_N).map((row, index) => ({
    label: row.sector,
    weight: row.weight * 100,
    color: DONUT_COLORS[index % DONUT_COLORS.length] ?? "var(--chart-1)",
  }));
  const othersWeight = sorted.slice(TOP_N).reduce((sum, row) => sum + row.weight, 0) * 100;
  return othersWeight > 0
    ? [...top, { label: othersLabel, weight: othersWeight, color: OTHERS_COLOR }]
    : top;
}
