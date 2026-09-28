import type { Icon } from "@phosphor-icons/react";
import { ChartBarIcon, ChartLineIcon, ChatCircleIcon, FunnelIcon } from "@phosphor-icons/react";

/** Primary nav items (docs/web/README.md §2). 對話/Chat and 個股/Stock both have real
 * destinations (Stock's index guides into the fully-built `/stock/{symbol}` workbench — see
 * `stock/stock-empty-state.tsx`); 大盤/Market is the existing stub; 選股/Screener routes to a
 * minimal "coming soon" placeholder page (honest, not fake functionality). 設定/Settings is
 * pinned separately at the bottom of the rail. `messageKey` indexes the `nav` i18n namespace. */
export type NavItem = {
  href: "/chat" | "/market" | "/stock" | "/screener";
  messageKey: "chat" | "market" | "stock" | "screener";
  icon: Icon;
  /** True once the destination is a real, built surface (drives no fake affordance, just intent). */
  real: boolean;
};

export const NAV_ITEMS: readonly NavItem[] = [
  { href: "/chat", messageKey: "chat", icon: ChatCircleIcon, real: true },
  { href: "/market", messageKey: "market", icon: ChartBarIcon, real: false },
  { href: "/stock", messageKey: "stock", icon: ChartLineIcon, real: true },
  { href: "/screener", messageKey: "screener", icon: FunnelIcon, real: false },
];
