import type { Icon } from "@phosphor-icons/react";
import {
  BinocularsIcon,
  ChartBarIcon,
  ChartLineIcon,
  ChatCircleIcon,
  FunnelIcon,
} from "@phosphor-icons/react";

/** Primary nav items. 對話/Chat, 個股/Stock, and 選股/Screener all have
 * real destinations (Stock's index guides into the fully-built `/stock/{symbol}` workbench — see
 * `stock/stock-empty-state.tsx`; Screener is the funnel workbench, `screener/screener-workbench.tsx`);
 * 大盤/Market is the existing stub. 設定/Settings is pinned separately at the bottom of the rail.
 * `messageKey` indexes the `nav` i18n namespace. */
export type NavItem = {
  href: "/chat" | "/market" | "/stock" | "/screener" | "/watch";
  messageKey: "chat" | "market" | "stock" | "screener" | "watch";
  icon: Icon;
  /** True once the destination is a real, built surface (drives no fake affordance, just intent). */
  real: boolean;
};

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/chat", messageKey: "chat", icon: ChatCircleIcon, real: true },
  { href: "/market", messageKey: "market", icon: ChartBarIcon, real: false },
  { href: "/stock", messageKey: "stock", icon: ChartLineIcon, real: true },
  { href: "/screener", messageKey: "screener", icon: FunnelIcon, real: true },
  // AI 盯盤: the strategy-watch dashboard (`components/watch/watch-dashboard.tsx`).
  { href: "/watch", messageKey: "watch", icon: BinocularsIcon, real: true },
];
