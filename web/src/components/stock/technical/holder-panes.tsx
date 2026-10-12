"use client";

import { useQuery } from "@tanstack/react-query";
import { LineSeries, type Time } from "lightweight-charts";
import type { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useRef } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { type RangedPaneProps, useLineChartPane } from "@/components/stock/technical/shared";
import { useInstitutionalFlow } from "@/hooks/use-stock-data";
import { getForeignShareholding, getShareholderDispersion } from "@/lib/api/stock-detail";
import { resolveToken } from "@/lib/resolve-css-token";
import { isMaskedTeaser } from "@/types/market";

// 外資 (Foreign_Investor + Foreign_Dealer_Self) / 投信 (Investment_Trust) / 自營商 (Dealer_self +
// Dealer_Hedging) — the same 3-bucket collapse `chip-analysis-panel.tsx`'s own
// `BUCKET_BY_INVESTOR`/`bucketedFlowByDate` use, duplicated here rather than imported since
// that module doesn't export them and this is the one other place in the app plotting the same
// 三大法人 buckets.
type InstitutionalBucket = "foreign" | "trust" | "dealer";

const INSTITUTIONAL_BUCKET_BY_INVESTOR: Record<string, InstitutionalBucket> = {
  Foreign_Investor: "foreign",
  Foreign_Dealer_Self: "foreign",
  Investment_Trust: "trust",
  Dealer_self: "dealer",
  Dealer_Hedging: "dealer",
};

const INSTITUTIONAL_BUCKET_COLOR: Record<InstitutionalBucket, string> = {
  foreign: "var(--chart-1)",
  trust: "var(--chart-2)",
  dealer: "var(--chart-3)",
};

export function InstitutionalFlowPane({
  symbol,
  market,
  startDate,
  endDate,
  t,
}: RangedPaneProps & { readonly t: ReturnType<typeof useTranslations> }): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useInstitutionalFlow(symbol, market);
  const rows = (query.data?.rows ?? []).filter(
    (r) => r.trade_date >= startDate && r.trade_date <= endDate,
  );

  // Net buy-sell per bucket per day, then a cumulative running total — the buy/sell delta alone
  // is too noisy day-to-day to read as a trend line; the reference design's 三大法人 line reads
  // as an accumulated position, not a daily net.
  const byDate = useMemo(() => {
    const dayTotals = new Map<string, Record<InstitutionalBucket, number>>();
    for (const row of rows) {
      const bucket = INSTITUTIONAL_BUCKET_BY_INVESTOR[row.investor];
      if (!bucket) continue;
      const entry = dayTotals.get(row.trade_date) ?? { foreign: 0, trust: 0, dealer: 0 };
      entry[bucket] += row.buy - row.sell;
      dayTotals.set(row.trade_date, entry);
    }
    const dates = [...dayTotals.keys()].sort((a, b) => a.localeCompare(b));
    let foreignCum = 0;
    let trustCum = 0;
    let dealerCum = 0;
    return dates.map((date) => {
      const entry = dayTotals.get(date);
      foreignCum += entry?.foreign ?? 0;
      trustCum += entry?.trust ?? 0;
      dealerCum += entry?.dealer ?? 0;
      return { date, foreign: foreignCum, trust: trustCum, dealer: dealerCum };
    });
  }, [rows]);

  useLineChartPane(
    containerRef,
    (chart) => {
      for (const bucket of ["foreign", "trust", "dealer"] as const) {
        const line = chart.addSeries(LineSeries, {
          color: resolveToken(INSTITUTIONAL_BUCKET_COLOR[bucket]),
          lineWidth: 1,
          priceLineVisible: false,
        });
        line.setData(byDate.map((p) => ({ time: p.date as Time, value: p[bucket] })));
      }
    },
    [byDate],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (byDate.length === 0) return null;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap gap-3">
        {(["foreign", "trust", "dealer"] as const).map((bucket) => (
          <span
            key={bucket}
            className="inline-flex items-center gap-1.5 text-[11px]"
            style={{ color: INSTITUTIONAL_BUCKET_COLOR[bucket] }}
          >
            <span
              aria-hidden
              className="inline-block h-0.5 w-3 rounded-full"
              style={{ backgroundColor: INSTITUTIONAL_BUCKET_COLOR[bucket] }}
            />
            {t(`institutionalBucket.${bucket}`)}
          </span>
        ))}
      </div>
      <div ref={containerRef} className="h-32 w-full" />
    </div>
  );
}

export function ForeignHoldingPane({
  symbol,
  market,
  startDate,
  endDate,
}: RangedPaneProps): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["stock_foreign_shareholding", symbol, market, startDate, endDate],
    queryFn: () => getForeignShareholding(symbol, market, { startDate, endDate }),
  });
  const rows = [...(query.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );

  useLineChartPane(
    containerRef,
    (chart) => {
      const ratioLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-1)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      ratioLine.setData(
        rows.map((r) => ({ time: r.trade_date as Time, value: r.foreign_investment_shares_ratio })),
      );
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

export function BigHoldersPane({
  symbol,
  market,
  startDate,
  endDate,
  t,
}: RangedPaneProps & { readonly t: ReturnType<typeof useTranslations> }): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["shareholder_dispersion_big_holders", symbol, market, startDate, endDate],
    queryFn: () => getShareholderDispersion(symbol, market, { startDate, endDate }),
  });
  const data = query.data;
  const locked = data !== undefined && isMaskedTeaser(data);
  // 大戶持股 as the cumulative %-of-custody share held by every tier from 400張 up (matches
  // `get_holder_accumulation`'s own TIER_400 threshold convention — the "1,000-share-lot big
  // holder" figure this reference design's 大戶持股 checkbox names).
  const rows =
    data !== undefined && !isMaskedTeaser(data)
      ? data.rows
          .filter((r) => {
            const lower = Number.parseInt(
              r.holding_shares_level.split("-")[0]?.replaceAll(",", "") ?? "",
              10,
            );
            return Number.isFinite(lower) && lower >= 400_001;
          })
          .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
      : [];
  const byDate = useMemo(() => {
    const totals = new Map<string, number>();
    for (const row of rows) {
      totals.set(row.trade_date, (totals.get(row.trade_date) ?? 0) + row.percent);
    }
    return [...totals.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, percent]) => ({ date, percent }));
  }, [rows]);

  useLineChartPane(
    containerRef,
    (chart) => {
      const percentLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-5)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      percentLine.setData(byDate.map((p) => ({ time: p.date as Time, value: p.percent })));
    },
    [byDate],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (!data) return null;
  if (locked && isMaskedTeaser(data)) {
    return (
      <SponsorTeaser
        headline={t("overlay.bigHolders")}
        preview={data.preview}
        requiredTier={data.required_tier}
      >
        <div className="h-32 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      </SponsorTeaser>
    );
  }
  if (byDate.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}
