"use client";

import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { getPriceHistory } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { getStockInstitutionalFlow } from "@/lib/api/stock-detail";
import type { Market } from "@/types/settings";

/** Reads several stock-detail panels share (chip analysis, technical, header). One hook per read,
 * so every panel shares one cache entry and one request. */

export function useInstitutionalFlow(
  symbol: string,
  market: Market,
  startDate?: string,
): UseQueryResult<Awaited<ReturnType<typeof getStockInstitutionalFlow>>> {
  return useQuery({
    queryKey: queryKeys.institutionalFlow(market, symbol, startDate),
    queryFn: () =>
      startDate === undefined
        ? getStockInstitutionalFlow(symbol, market)
        : getStockInstitutionalFlow(symbol, market, { startDate }),
  });
}

export function usePriceHistory(
  symbol: string,
  market: Market,
  days: number,
): UseQueryResult<Awaited<ReturnType<typeof getPriceHistory>>> {
  return useQuery({
    queryKey: queryKeys.priceHistory(market, symbol, days),
    queryFn: () => getPriceHistory([symbol], market, days),
  });
}
