"use client";

import { StarIcon } from "@phosphor-icons/react";
import * as Popover from "@radix-ui/react-popover";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  groupsForAssetClass,
  useAddWatchlistItem,
  useUnstarSymbolEverywhere,
  useWatchlists,
  watchesSymbol,
} from "@/hooks/use-watchlists";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";
import type { WatchlistAssetClass } from "@/types/watchlist";

/**
 * ☆ add-to-watchlist toggle on the stock/ETF detail header. Reuses the SAME `useWatchlists(market)`
 * query the rail (`WatchlistRail`) holds — no extra fetch just for the star's lit/unlit state.
 * Click behavior confirmed with the user: unstarred + exactly one group for this
 * `(market, asset_class)` → add directly; unstarred + more than one group → a small picker
 * popover; already starred anywhere in this tab → clicking removes it from every group it's in
 * (the star is one lit/unlit state per tab, mirroring the backend's own `watches()` reverse-query
 * semantics, not a per-group toggle).
 */
export function WatchlistStarButton({
  symbol,
  market,
  assetClass,
  size = "default",
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly assetClass: WatchlistAssetClass;
  /** `"default"` (44px, `AssetHeader`'s own size) vs `"compact"` (28px) — the same `size-7`
   * dense-row precedent `WatchlistStockRow`'s own remove-✕-button already established in
   * `watchlist-rail.tsx`, reused here for market/ETF table rows too dense for a 44px control. */
  readonly size?: "default" | "compact";
}): ReactNode {
  const t = useTranslations("stock.assetHeader");
  const watchlistsQuery = useWatchlists(market);
  const groups = groupsForAssetClass(watchlistsQuery.data ?? [], assetClass);
  const starred = watchesSymbol(groups, symbol);
  const addMutation = useAddWatchlistItem(market);
  const unstarMutation = useUnstarSymbolEverywhere(market);

  const disabled = watchlistsQuery.isLoading || addMutation.isPending || unstarMutation.isPending;
  const color = starred ? "var(--accent)" : "var(--text-muted)";
  const boxSize = size === "compact" ? "size-7" : "size-11";
  const iconSize = size === "compact" ? 14 : 18;

  const handleUnstar = (): void => {
    unstarMutation.mutate({ symbol, groups });
  };

  const handleAddToOnlyGroup = (groupId: string): void => {
    addMutation.mutate({ watchlistId: groupId, symbol });
  };

  if (starred) {
    return (
      <button
        type="button"
        onClick={handleUnstar}
        disabled={disabled}
        title={t("removeFromWatchlist")}
        aria-label={t("removeFromWatchlist")}
        aria-pressed
        className={cn(
          "inline-flex items-center justify-center rounded-[var(--radius-button)] transition-colors hover:bg-[var(--surface-2)]",
          boxSize,
        )}
      >
        <StarIcon size={iconSize} weight="fill" color={color} aria-hidden />
      </button>
    );
  }

  if (groups.length <= 1) {
    return (
      <button
        type="button"
        onClick={() => {
          const only = groups[0];
          if (only) handleAddToOnlyGroup(only.group.id);
        }}
        disabled={disabled || groups.length === 0}
        title={groups.length === 0 ? t("noWatchlistGroups") : t("addToWatchlist")}
        aria-label={t("addToWatchlist")}
        aria-pressed={false}
        className={cn(
          "inline-flex items-center justify-center rounded-[var(--radius-button)] transition-colors hover:bg-[var(--surface-2)]",
          boxSize,
          groups.length === 0 && "opacity-50",
        )}
      >
        <StarIcon size={iconSize} weight="regular" color={color} aria-hidden />
      </button>
    );
  }

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          type="button"
          disabled={disabled}
          title={t("addToWatchlist")}
          aria-label={t("addToWatchlist")}
          aria-pressed={false}
          className={cn(
            "inline-flex items-center justify-center rounded-[var(--radius-button)] transition-colors hover:bg-[var(--surface-2)]",
            boxSize,
          )}
        >
          <StarIcon size={iconSize} weight="regular" color={color} aria-hidden />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          align="end"
          className="z-50 min-w-[180px] rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-1.5 shadow-lg"
        >
          <p className="px-2.5 py-1.5 text-[11px] text-[var(--text-muted)]">
            {t("pickWatchlistGroup")}
          </p>
          <ul>
            {groups.map((view) => (
              <li key={view.group.id}>
                <Popover.Close asChild>
                  <button
                    type="button"
                    onClick={() => handleAddToOnlyGroup(view.group.id)}
                    className="flex min-h-11 w-full items-center rounded-[var(--radius-input)] px-2.5 text-left text-[13px] text-[var(--text)] transition-colors hover:bg-[var(--surface-2)]"
                  >
                    {view.group.name}
                  </button>
                </Popover.Close>
              </li>
            ))}
          </ul>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
