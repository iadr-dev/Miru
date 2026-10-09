"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addWatchlistItem,
  createWatchlist,
  deleteWatchlist,
  getWatchlists,
  moveWatchlistItem,
  removeWatchlistItem,
  renameWatchlist,
  reorderWatchlistGroup,
  setWatchlistColor,
  WatchlistApiError,
} from "@/lib/api/watchlist";
import type { Market } from "@/types/settings";
import type { WatchlistAssetClass, WatchlistColor, WatchlistGroupView } from "@/types/watchlist";

const QUERY_KEY_ROOT = "watchlists" as const;

function queryKey(market: Market): readonly [typeof QUERY_KEY_ROOT, Market] {
  return [QUERY_KEY_ROOT, market];
}

/** One market's watchlist groups+items — `market` is the query boundary (mirrors `GET
 * /watchlists?market=`); ETF and stock groups for the same market share one query, since the
 * backend's own `asset_class` split is a client-side filter over the same response, not a
 * separate fetch. */
export function useWatchlists(market: Market) {
  return useQuery({
    queryKey: queryKey(market),
    queryFn: () => getWatchlists(market),
  });
}

/** `groups` filtered to one tab's `asset_class` — the TW/US/ETF rail tabs map to `(market,
 * asset_class)` per ADR-0027, not a 3-value enum. */
export function groupsForAssetClass(
  groups: readonly WatchlistGroupView[],
  assetClass: WatchlistAssetClass,
): WatchlistGroupView[] {
  return groups.filter((view) => view.group.asset_class === assetClass);
}

/** Does the user watch `symbol` anywhere in `groups` — the star's lit/unlit state, computed
 * client-side from the same query the rail already holds (no extra fetch per star button). */
export function watchesSymbol(groups: readonly WatchlistGroupView[], symbol: string): boolean {
  return groups.some((view) => view.items.some((item) => item.symbol === symbol));
}

function invalidate(queryClient: ReturnType<typeof useQueryClient>, market: Market): void {
  queryClient.invalidateQueries({ queryKey: queryKey(market) });
}

/** Optimistic add: the item appears in the target group immediately; a real failure (network,
 * or a 409 if the group was concurrently deleted) rolls back to the pre-mutation snapshot —
 * `web/CLAUDE.md` §5's "optimistic + reconcile" convention, watchlist star is its own named
 * example there. */
export function useAddWatchlistItem(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ watchlistId, symbol }: { watchlistId: string; symbol: string }) =>
      addWatchlistItem(watchlistId, symbol),
    onMutate: async ({ watchlistId, symbol }) => {
      await queryClient.cancelQueries({ queryKey: queryKey(market) });
      const previous = queryClient.getQueryData<WatchlistGroupView[]>(queryKey(market));
      queryClient.setQueryData<WatchlistGroupView[]>(queryKey(market), (current) =>
        (current ?? []).map((view) =>
          view.group.id === watchlistId && !view.items.some((item) => item.symbol === symbol)
            ? {
                ...view,
                items: [
                  ...view.items,
                  {
                    id: `optimistic-${symbol}`,
                    watchlist_id: watchlistId,
                    user_id: view.group.user_id,
                    symbol,
                    market: view.group.market,
                    asset_class: view.group.asset_class,
                    note: null,
                    sort_order: "",
                    added_at: new Date().toISOString(),
                  },
                ],
              }
            : view,
        ),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey(market), context.previous);
      }
    },
    onSettled: () => invalidate(queryClient, market),
  });
}

export function useRemoveWatchlistItem(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ watchlistId, symbol }: { watchlistId: string; symbol: string }) =>
      removeWatchlistItem(watchlistId, symbol),
    onMutate: async ({ watchlistId, symbol }) => {
      await queryClient.cancelQueries({ queryKey: queryKey(market) });
      const previous = queryClient.getQueryData<WatchlistGroupView[]>(queryKey(market));
      queryClient.setQueryData<WatchlistGroupView[]>(queryKey(market), (current) =>
        (current ?? []).map((view) =>
          view.group.id === watchlistId
            ? { ...view, items: view.items.filter((item) => item.symbol !== symbol) }
            : view,
        ),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey(market), context.previous);
      }
    },
    onSettled: () => invalidate(queryClient, market),
  });
}

/** Removes `symbol` from every group in `market` — the star toggle's own "remove anywhere in
 * this tab" semantics (mirrors `watches()`'s reverse-query being tab-wide, not per-group). */
export function useUnstarSymbolEverywhere(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      symbol,
      groups,
    }: {
      symbol: string;
      groups: readonly WatchlistGroupView[];
    }) => {
      const owners = groups.filter((view) => view.items.some((item) => item.symbol === symbol));
      await Promise.all(owners.map((view) => removeWatchlistItem(view.group.id, symbol)));
    },
    onMutate: async ({ symbol }) => {
      await queryClient.cancelQueries({ queryKey: queryKey(market) });
      const previous = queryClient.getQueryData<WatchlistGroupView[]>(queryKey(market));
      queryClient.setQueryData<WatchlistGroupView[]>(queryKey(market), (current) =>
        (current ?? []).map((view) => ({
          ...view,
          items: view.items.filter((item) => item.symbol !== symbol),
        })),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey(market), context.previous);
      }
    },
    onSettled: () => invalidate(queryClient, market),
  });
}

export function useCreateWatchlist(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ name, assetClass }: { name: string; assetClass: WatchlistAssetClass }) =>
      createWatchlist(name, market, assetClass),
    onSuccess: () => invalidate(queryClient, market),
  });
}

export function useRenameWatchlist(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      watchlistId,
      name,
      version,
    }: {
      watchlistId: string;
      name: string;
      version: number;
    }) => renameWatchlist(watchlistId, name, version),
    onMutate: async ({ watchlistId, name }) => {
      await queryClient.cancelQueries({ queryKey: queryKey(market) });
      const previous = queryClient.getQueryData<WatchlistGroupView[]>(queryKey(market));
      queryClient.setQueryData<WatchlistGroupView[]>(queryKey(market), (current) =>
        (current ?? []).map((view) =>
          view.group.id === watchlistId ? { ...view, group: { ...view.group, name } } : view,
        ),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey(market), context.previous);
      }
    },
    onSettled: () => invalidate(queryClient, market),
  });
}

export function useSetWatchlistColor(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      watchlistId,
      color,
      version,
    }: {
      watchlistId: string;
      color: WatchlistColor | null;
      version: number;
    }) => setWatchlistColor(watchlistId, color, version),
    onMutate: async ({ watchlistId, color }) => {
      await queryClient.cancelQueries({ queryKey: queryKey(market) });
      const previous = queryClient.getQueryData<WatchlistGroupView[]>(queryKey(market));
      queryClient.setQueryData<WatchlistGroupView[]>(queryKey(market), (current) =>
        (current ?? []).map((view) =>
          view.group.id === watchlistId ? { ...view, group: { ...view.group, color } } : view,
        ),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey(market), context.previous);
      }
    },
    onSettled: () => invalidate(queryClient, market),
  });
}

export function useDeleteWatchlist(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (watchlistId: string) => deleteWatchlist(watchlistId),
    onMutate: async (watchlistId) => {
      await queryClient.cancelQueries({ queryKey: queryKey(market) });
      const previous = queryClient.getQueryData<WatchlistGroupView[]>(queryKey(market));
      queryClient.setQueryData<WatchlistGroupView[]>(queryKey(market), (current) =>
        (current ?? []).filter((view) => view.group.id !== watchlistId),
      );
      return { previous };
    },
    onError: (_error, _vars, context) => {
      if (context?.previous) {
        queryClient.setQueryData(queryKey(market), context.previous);
      }
    },
    onSettled: () => invalidate(queryClient, market),
  });
}

export function useReorderWatchlistGroup(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      watchlistId,
      afterId,
      beforeId,
      version,
    }: {
      watchlistId: string;
      afterId: string | null;
      beforeId: string | null;
      version: number;
    }) => reorderWatchlistGroup(watchlistId, afterId, beforeId, version),
    onSettled: () => invalidate(queryClient, market),
  });
}

export function useMoveWatchlistItem(market: Market) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      itemId,
      currentWatchlistId,
      targetWatchlistId,
      afterId,
    }: {
      itemId: string;
      currentWatchlistId: string;
      targetWatchlistId: string;
      afterId?: string | null;
    }) => moveWatchlistItem(itemId, currentWatchlistId, targetWatchlistId, afterId),
    onSettled: () => invalidate(queryClient, market),
  });
}

export { WatchlistApiError };
