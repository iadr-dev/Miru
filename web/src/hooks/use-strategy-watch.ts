"use client";

import {
  type UseMutationResult,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/query-keys";
import {
  getStrategySubscription,
  getStrategyWatchToday,
  putStrategySubscription,
} from "@/lib/api/strategy-watch";
import { FRESHNESS } from "@/lib/freshness";
import type {
  StrategySubscription,
  StrategySubscriptionInput,
  StrategyWatchToday,
} from "@/types/strategy-watch";

/** Intraday checks land every 10 minutes during the session, so the list refetches each minute
 * while the page is open (one small read). */
const REFRESH_MS = 60_000;

export function useStrategyWatch(asOf?: string): UseQueryResult<StrategyWatchToday> {
  return useQuery({
    queryKey: queryKeys.strategyWatch(asOf),
    queryFn: () => getStrategyWatchToday({ asOf }),
    staleTime: FRESHNESS.slow,
    refetchInterval: REFRESH_MS,
  });
}

export function useStrategySubscription(): UseQueryResult<StrategySubscription | null> {
  return useQuery({
    queryKey: queryKeys.strategySubscription,
    queryFn: getStrategySubscription,
    staleTime: FRESHNESS.standard,
  });
}

export function useSaveStrategySubscription(): UseMutationResult<
  StrategySubscription,
  Error,
  StrategySubscriptionInput
> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: putStrategySubscription,
    onSuccess: (saved) => {
      client.setQueryData(queryKeys.strategySubscription, saved);
    },
  });
}
