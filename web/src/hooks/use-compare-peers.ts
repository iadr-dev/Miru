"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useThemeMembers } from "@/hooks/use-themes";
import { queryKeys } from "@/lib/api/query-keys";
import { getSymbolThemes } from "@/lib/api/stock-detail";
import { FRESHNESS } from "@/lib/freshness";
import { pickPeers } from "@/lib/stock-compare";
import type { Market } from "@/types/settings";

const PEER_COUNT = 2;

/** Up to two peers for the compare view's default list: other stocks in the symbol's first concept
 * theme (the 所屬題材 the stock page already shows). Both reads share the cache with the industry
 * tab. Empty while loading, when the symbol is in no concept theme, or when `enabled` is off (the
 * URL already names the symbols) — the view then simply starts from the current stock alone. */
export function useComparePeers(symbol: string, market: Market, enabled: boolean): string[] {
  const themesQuery = useQuery({
    queryKey: queryKeys.symbolRead("symbol_themes", symbol, market),
    queryFn: () => getSymbolThemes(symbol, market),
    enabled,
    staleTime: FRESHNESS.reference,
  });
  const conceptKey = useMemo(
    () =>
      (themesQuery.data?.themes ?? [])
        .filter((theme) => theme.kind === "concept")
        .sort((a, b) => b.confidence - a.confidence)
        .at(0)?.theme_key ?? null,
    [themesQuery.data],
  );
  const membersQuery = useThemeMembers(enabled ? conceptKey : null);
  const members = membersQuery.data?.members;
  return useMemo(
    () => (enabled && members ? pickPeers(members, symbol, PEER_COUNT) : []),
    [enabled, members, symbol],
  );
}
