"use client";

/**
 * Keeps the market data on screen current when ingest lands new data: subscribes to the
 * `data.updated` feed (`GET /updates/stream`) and refreshes the cached reads it touches. Which
 * reads, and the coalescing, the random spread and the hidden-tab hold that keep it from
 * becoming a load spike, are in `lib/data-update-refresh.ts`.
 *
 * Mounted once for the whole signed-in app (`components/providers/data-updates-bridge.tsx`). The
 * server keeps one subscription per API process however many tabs are open, so a tab costs it
 * nothing but the stream.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { subscribeDataUpdates } from "@/lib/api/data-updates";
import { ALL_MARKETS, createRefreshScheduler, isMarketDataQuery } from "@/lib/data-update-refresh";

export function useDataUpdates(): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    const scheduler = createRefreshScheduler({
      run: (markets) => {
        void queryClient.invalidateQueries({
          predicate: (query) => isMarketDataQuery(query.queryKey, markets),
          refetchType: "active",
        });
      },
    });
    const handle = subscribeDataUpdates(
      (event) => scheduler.add(event.data.market),
      // Signals sent while the connection was down are lost: refresh everything once it is back.
      { onReconnect: () => scheduler.add(ALL_MARKETS) },
    );
    const onVisible = (): void => {
      if (!document.hidden) {
        scheduler.flushIfVisible();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      scheduler.cancel();
      handle.close();
    };
  }, [queryClient]);
}
