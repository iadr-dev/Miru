/**
 * Query keys for reads that more than one component makes. Every caller uses the same factory,
 * so the same request is always the same cache entry (one fetch, one result), and invalidation can
 * target a resource by prefix. Order: resource, then market, then symbol, then the arguments that
 * change the result.
 */
import type { Market } from "@/types/settings";

export const queryKeys = {
  institutionalFlow: (market: Market, symbol: string, startDate?: string) =>
    startDate === undefined
      ? (["stock_institutional_flow", market, symbol] as const)
      : (["stock_institutional_flow", market, symbol, startDate] as const),
  priceHistory: (market: Market, symbol: string, days: number) =>
    ["price_history", market, symbol, days] as const,
  themeMembers: (themeKey: string) => ["theme_members", themeKey] as const,
  themes: (kind: string) => ["themes", kind] as const,
};
