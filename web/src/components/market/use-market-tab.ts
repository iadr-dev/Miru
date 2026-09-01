"use client";

import { create } from "zustand";
import type { MarketTab } from "@/components/market/market-tabs";

/** The active 台股/美股/ETF tab, shared between `MarketTabs` (rendered in `TopBar` on `/market`
 * only) and `MarketPanel` (decides which market's view to render) — two different components in
 * the tree need the same selection, so it's a small Zustand store rather than prop-drilled state,
 * mirroring `useSidebarCollapsed`/`useWatchlistOpen`'s shape but without persistence (this is a
 * page-local UI choice, not a durable user preference). */
export const useMarketTab = create<{
  tab: MarketTab;
  setTab: (tab: MarketTab) => void;
}>((set) => ({
  tab: "tw",
  setTab: (tab) => set({ tab }),
}));
