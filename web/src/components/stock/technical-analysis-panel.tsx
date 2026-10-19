"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { EventList } from "@/components/stock/technical/event-list";
import { KLineChart } from "@/components/stock/technical/k-line-chart";
import type {
  MaWindow,
  RangeKey,
  SubOverlay,
  Timeframe,
} from "@/components/stock/technical/shared";
import {
  MaToggleRow,
  OverlayToggleRow,
  RangeToggle,
  StructureToggleRow,
  TimeframeToggle,
} from "@/components/stock/technical/toggles";
import { QueryFailure } from "@/components/ui/query-state";
import { useChartEvents } from "@/hooks/use-chart-events";
import { queryKeys } from "@/lib/api/query-keys";
import { getTechnicalIndicators } from "@/lib/api/stock-detail";
import type { Market } from "@/types/settings";
import type { EodBar } from "@/types/stock/technical";

function rangeStartDate(range: RangeKey, today: Date): string {
  const d = new Date(today);
  switch (range) {
    case "1m":
      d.setMonth(d.getMonth() - 1);
      break;
    case "3m":
      d.setMonth(d.getMonth() - 3);
      break;
    case "6m":
      d.setMonth(d.getMonth() - 6);
      break;
    case "ytd":
      return `${today.getFullYear()}-01-01`;
    case "1y":
      d.setFullYear(d.getFullYear() - 1);
      break;
    case "5y":
      d.setFullYear(d.getFullYear() - 5);
      break;
    default:
      break;
  }
  return d.toISOString().slice(0, 10);
}

export function TechnicalAnalysisPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.technical");
  const [timeframe, setTimeframe] = useState<Timeframe>("day");
  const [range, setRange] = useState<RangeKey>("1y");
  const [visibleMa, setVisibleMa] = useState<ReadonlySet<MaWindow>>(new Set([5, 10, 20]));
  // A clean first view, like TradingView: only MA5/10/20 are on; every other overlay is opt-in.
  const [subOverlays, setSubOverlays] = useState<ReadonlySet<SubOverlay>>(new Set());
  const [showSupportResistance, setShowSupportResistance] = useState(false);
  const [showPatterns, setShowPatterns] = useState(false);
  const [showEvents, setShowEvents] = useState(true);

  const today = useMemo(() => new Date(), []);
  const startDate = useMemo(() => rangeStartDate(range, today), [range, today]);
  const endDate = useMemo(() => today.toISOString().slice(0, 10), [today]);

  const query = useQuery({
    queryKey: queryKeys.symbolRead("technical_indicators", symbol, market, startDate, endDate),
    queryFn: () => getTechnicalIndicators(symbol, market, { startDate, endDate }),
  });

  const chartEvents = useChartEvents(symbol, market);
  const hasEventFeed = market === "TW";

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <CardSkeletonBody height={480} />
      </section>
    );
  }
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const data = query.data;
  if (!data?.data_available || data.bars.length === 0) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      </section>
    );
  }

  // `timeframe` (日/週/月) only re-buckets the SAME fetched daily bars client-side (see
  // `bucketBars` below) — no separate weekly/monthly fetch, since only a daily
  // `Capability.PRICE_EOD` window is available at every range without a Sponsor gate.
  const bucketed = bucketBars(data.bars, timeframe);

  return (
    <section className="surface-card p-4 sm:p-5">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <div className="flex flex-wrap items-center gap-3">
          <TimeframeToggle value={timeframe} onChange={setTimeframe} t={t} />
          <RangeToggle value={range} onChange={setRange} t={t} />
        </div>
      </div>

      <MaToggleRow visible={visibleMa} onToggle={setVisibleMa} t={t} />
      <StructureToggleRow
        showSupportResistance={showSupportResistance}
        onToggleSupportResistance={setShowSupportResistance}
        showPatterns={showPatterns}
        onTogglePatterns={setShowPatterns}
        showEvents={hasEventFeed ? showEvents : undefined}
        onToggleEvents={setShowEvents}
        t={t}
      />
      <OverlayToggleRow value={subOverlays} onChange={setSubOverlays} market={market} t={t} />

      <KLineChart
        bars={bucketed}
        indicators={data}
        visibleMa={visibleMa}
        subOverlays={subOverlays}
        showSupportResistance={showSupportResistance}
        showPatterns={showPatterns}
        showEvents={hasEventFeed && showEvents}
        events={chartEvents.events}
        symbol={symbol}
        market={market}
        startDate={startDate}
        endDate={endDate}
        t={t}
      />

      {hasEventFeed ? (
        <EventList
          events={chartEvents.events}
          rangeStart={startDate}
          isLoading={chartEvents.isLoading}
          isError={chartEvents.isError}
          degraded={chartEvents.degraded}
          onRetry={chartEvents.refetch}
          t={t}
        />
      ) : null}

      <p className="mt-3 text-[11px] text-[var(--text-muted)]">{data.non_trading_caveat}</p>
    </section>
  );
}

/** Buckets daily `EodBar`s into weekly/monthly OHLCV bars for the 週/月 timeframe toggle — a
 * client-side re-aggregation of the one daily fetch, since a Sponsor-gated weekly/monthly
 * dataset is a separate, gated capability this public panel doesn't have access to (see the
 * survey this build was scoped against). ISO week (Mon-start) for 週, calendar month for 月. */
function bucketBars(bars: readonly EodBar[], timeframe: Timeframe): EodBar[] {
  if (timeframe === "day") return [...bars];
  const groups = new Map<string, EodBar[]>();
  for (const bar of bars) {
    const date = new Date(`${bar.trade_date}T00:00:00Z`);
    const key = timeframe === "month" ? bar.trade_date.slice(0, 7) : isoWeekKey(date);
    const group = groups.get(key) ?? [];
    group.push(bar);
    groups.set(key, group);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([, group]) => {
      const sorted = [...group].sort((a, b) => a.trade_date.localeCompare(b.trade_date));
      const first = sorted[0];
      const last = sorted.at(-1);
      if (!first || !last) {
        throw new Error("bucketBars: empty group");
      }
      return {
        symbol: first.symbol,
        market: first.market,
        trade_date: last.trade_date,
        open: first.open,
        high: Math.max(...sorted.map((b) => b.high)),
        low: Math.min(...sorted.map((b) => b.low)),
        close: last.close,
        volume: sorted.reduce((sum, b) => sum + b.volume, 0),
      };
    });
}

function isoWeekKey(date: Date): string {
  const d = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()));
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() - dayNum + 1);
  return d.toISOString().slice(0, 10);
}
