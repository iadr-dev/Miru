"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { queryKeys } from "@/lib/api/query-keys";
import { getDividendPolicySummary, getStockCalendarEvents } from "@/lib/api/stock-detail";
import { buildChartEvents, type ChartEvent } from "@/lib/chart-events";
import { FRESHNESS } from "@/lib/freshness";
import type { Market } from "@/types/settings";

function isoOffset(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString().slice(0, 10);
}

export interface ChartEventsResult {
  readonly events: readonly ChartEvent[];
  readonly isLoading: boolean;
  readonly isError: boolean;
  /** One of the sources failed or flagged itself stale / incomplete. */
  readonly degraded: boolean;
  readonly refetch: () => void;
}

/** Corporate events for the candlestick chart (TW only — both feeds are TW-sourced). Shares the
 * query keys and date windows `basic-info-panel.tsx` / `research-charts-panel.tsx` (calendar) and
 * `dividend-policy-card.tsx` (`5y`) already use, so opening those tabs costs no extra request. */
export function useChartEvents(symbol: string, market: Market): ChartEventsResult {
  const enabled = market === "TW";
  const calendar = useQuery({
    queryKey: queryKeys.symbolRead("stock_calendar_events", symbol, market),
    queryFn: () =>
      getStockCalendarEvents(symbol, market, {
        startDate: isoOffset(-365),
        endDate: isoOffset(730),
      }),
    staleTime: FRESHNESS.reference,
    enabled,
  });
  const dividends = useQuery({
    queryKey: queryKeys.symbolRead("dividend_policy_summary", symbol, market, "5y"),
    queryFn: () => getDividendPolicySummary(symbol, market, { startDate: isoOffset(-365 * 5) }),
    staleTime: FRESHNESS.reference,
    enabled,
  });

  const calendarEvents = calendar.data?.events;
  const dividendEvents = dividends.data?.events;
  const events = useMemo(
    () => buildChartEvents(calendarEvents ?? [], dividendEvents ?? [], isoOffset(0)),
    [calendarEvents, dividendEvents],
  );

  const oneFailed = calendar.isError !== dividends.isError;
  const staleSource =
    (calendar.data !== undefined && calendar.data.health_status !== "ok") ||
    (dividends.data !== undefined && dividends.data.health_status !== "ok");
  return {
    events,
    isLoading: enabled && (calendar.isLoading || dividends.isLoading),
    isError: calendar.isError && dividends.isError,
    // One source failing while the other answers is an incomplete list, not an error.
    degraded: oneFailed || staleSource,
    refetch: () => {
      void calendar.refetch();
      void dividends.refetch();
    },
  };
}
