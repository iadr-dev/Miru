"use client";

import { useQuery } from "@tanstack/react-query";
import { LineSeries, type Time } from "lightweight-charts";
import type { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useRef } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { type RangedPaneProps, useLineChartPane } from "@/components/stock/technical/shared";
import {
  getSecuritiesLending,
  getShareholderDispersion,
  getStockMarginShort,
} from "@/lib/api/stock-detail";
import { resolveToken } from "@/lib/resolve-css-token";
import { isMaskedTeaser } from "@/types/market/shared";

export function MarginShortPane({
  symbol,
  market,
  startDate,
  endDate,
}: RangedPaneProps): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["margin_short", symbol, market, startDate, endDate],
    queryFn: () => getStockMarginShort(symbol, market, { startDate, endDate }),
  });
  const rows = [...(query.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );

  useLineChartPane(
    containerRef,
    (chart) => {
      const marginLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-1)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      marginLine.setData(
        rows.map((r) => ({ time: r.trade_date as Time, value: r.margin_purchase_today_balance })),
      );
      const shortLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-4)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      shortLine.setData(
        rows.map((r) => ({ time: r.trade_date as Time, value: r.short_sale_today_balance })),
      );
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

export function SecuritiesLendingPane({
  symbol,
  market,
  startDate,
  endDate,
}: RangedPaneProps): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["securities_lending", symbol, market, startDate, endDate],
    queryFn: () => getSecuritiesLending(symbol, market, { startDate, endDate }),
  });
  // `get_securities_lending` can carry more than one row per `trade_date` (distinct
  // `transaction_type`s) — lightweight-charts requires strictly-ascending, unique timestamps
  // per series, so same-day rows are summed into one point rather than plotted as duplicates.
  const rows = useMemo(() => {
    const byDate = new Map<string, number>();
    for (const row of query.data?.rows ?? []) {
      byDate.set(row.trade_date, (byDate.get(row.trade_date) ?? 0) + row.volume);
    }
    return [...byDate.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([trade_date, volume]) => ({ trade_date, volume }));
  }, [query.data]);

  useLineChartPane(
    containerRef,
    (chart) => {
      const volumeLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-2)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      volumeLine.setData(rows.map((r) => ({ time: r.trade_date as Time, value: r.volume })));
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}

export function RetailDispersionPane({
  symbol,
  market,
  startDate,
  endDate,
  t,
}: RangedPaneProps & { readonly t: ReturnType<typeof useTranslations> }): ReactNode {
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useQuery({
    queryKey: ["shareholder_dispersion", symbol, market, startDate, endDate],
    queryFn: () => getShareholderDispersion(symbol, market, { startDate, endDate }),
  });
  const data = query.data;
  const locked = data !== undefined && isMaskedTeaser(data);
  // 散戶 as a percent-of-custody share (小額 <1000 shares tiers) rather than raw people-count —
  // the count alone can't be plotted alongside a percent series on one axis, and percent is the
  // more directly comparable "散戶 concentration" read across dates.
  const rows =
    data !== undefined && !isMaskedTeaser(data)
      ? data.rows
          .filter(
            (r) =>
              r.holding_shares_level.includes("1-999") || r.holding_shares_level.includes("999"),
          )
          .sort((a, b) => a.trade_date.localeCompare(b.trade_date))
      : [];

  useLineChartPane(
    containerRef,
    (chart) => {
      const percentLine = chart.addSeries(LineSeries, {
        color: resolveToken("var(--chart-3)"),
        lineWidth: 1,
        priceLineVisible: false,
      });
      percentLine.setData(rows.map((r) => ({ time: r.trade_date as Time, value: r.percent })));
    },
    [rows],
  );

  if (query.isLoading) return <CardSkeletonBody height={128} />;
  if (!data) return null;
  if (locked && isMaskedTeaser(data)) {
    return (
      <SponsorTeaser
        headline={t("overlay.retail")}
        preview={data.preview}
        requiredTier={data.required_tier}
      >
        <div className="h-32 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      </SponsorTeaser>
    );
  }
  if (rows.length === 0) return null;
  return <div ref={containerRef} className="h-32 w-full" />;
}
