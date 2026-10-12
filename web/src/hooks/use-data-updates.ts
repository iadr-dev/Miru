"use client";

/**
 * Subscribes to the `data.updated` cache-invalidation feed (`GET /updates/stream`) and
 * invalidates every TanStack Query cache entry the signal names, so a query-key convention of
 * `[capability, market, ...]` (web/OVERVIEW.md §3: "query keys mirror the backend key `(capability,
 * market, symbol[, date])`") gets pushed a fresh refetch the moment the ingest leader's write
 * actually lands — not just whenever `staleTime` happens to next expire.
 *
 * Currently UNUSED: no page queries market/screener/stock capability data through TanStack Query
 * yet (those routes are still `ComingSoon` stubs; the only present-day capability reads are inside
 * one-shot chat rich-cards, which have no persisted query-cache entry to invalidate). Mount this
 * hook once near the root of whichever page first owns a real `[capability, market, ...]`-keyed
 * `useQuery` — the market dashboard is the first candidate — rather than mounting it app-wide
 * pre-emptively with nothing subscribed.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { subscribeDataUpdates } from "@/lib/api/data-updates";

export function useDataUpdates(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const handle = subscribeDataUpdates((event) => {
      const { capability, market } = event.data;
      void queryClient.invalidateQueries({
        predicate: (query) => query.queryKey[0] === capability && query.queryKey[1] === market,
      });
    });
    return () => handle.close();
  }, [queryClient]);
}
