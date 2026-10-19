"use client";

import { type UseQueryResult, useQueries, useQuery } from "@tanstack/react-query";
import { getPriceHistory } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import { getStockInstitutionalFlow } from "@/lib/api/stock-detail";
import { FRESHNESS } from "@/lib/freshness";
import type { PriceHistoryResponse } from "@/types/market/quotes";
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

export type PriceHistoriesState = {
  /** One entry per requested symbol, in order; `undefined` until that symbol's read lands. */
  readonly histories: readonly (PriceHistoryResponse | undefined)[];
  readonly isLoading: boolean;
  readonly isError: boolean;
  readonly error: unknown;
  /** Retry only the reads that failed. */
  readonly retry: () => void;
};

function combinePriceHistories(
  results: UseQueryResult<PriceHistoryResponse>[],
): PriceHistoriesState {
  return {
    histories: results.map((result) => result.data),
    isLoading: results.some((result) => result.isLoading),
    isError: results.some((result) => result.isError),
    error: results.find((result) => result.isError)?.error ?? null,
    retry: () => {
      for (const result of results) {
        if (result.isError) {
          void result.refetch();
        }
      }
    },
  };
}

/** The same per-symbol read as `usePriceHistory`, for several symbols at once: each symbol keeps
 * its own cache entry (`queryKeys.priceHistory`), so a symbol already loaded by another panel — or
 * kept when the list changes — is not fetched again, and one failing symbol does not blank the
 * rest. End-of-day data: `FRESHNESS.reference`, refreshed early by the `data.updated` push. */
export function usePriceHistories(
  symbols: readonly string[],
  market: Market,
  days: number,
): PriceHistoriesState {
  return useQueries({
    queries: symbols.map((symbol) => ({
      queryKey: queryKeys.priceHistory(market, symbol, days),
      queryFn: () => getPriceHistory([symbol], market, days),
      staleTime: FRESHNESS.reference,
    })),
    combine: combinePriceHistories,
  });
}
