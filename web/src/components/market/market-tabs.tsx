"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useMarketTab } from "@/components/market/use-market-tab";
import { cn } from "@/lib/cn";

export const MARKET_TABS = ["tw", "us", "etf"] as const;
export type MarketTab = (typeof MARKET_TABS)[number];

/**
 * The 台股/美股/ETF top-level switcher UI (mockup `.market-tabs`) — rendered from `TopBar` (only
 * on `/market`, see `MarketTabsTopBarSlot`), reading/writing the shared `useMarketTab` store so
 * `MarketPanel` (a sibling in the tree, not an ancestor/descendant of the top bar) sees the same
 * selection. Split from the tab CONTENT (`MarketTabPanels` below) because the switcher now lives
 * in the top bar while the panels stay in the page body — two different places in the tree.
 */
export function MarketTabSwitcher(): ReactNode {
  const t = useTranslations("market.tabs");
  const { tab: active, setTab } = useMarketTab();

  return (
    <div
      role="tablist"
      aria-label={t("label")}
      className="inline-flex w-fit gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-1"
    >
      {MARKET_TABS.map((tab) => (
        <button
          key={tab}
          type="button"
          role="tab"
          aria-selected={active === tab}
          onClick={() => setTab(tab)}
          className={cn(
            "min-h-11 rounded-[var(--radius-pill)] px-5 text-[13px] transition-colors",
            active === tab
              ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t(tab)}
        </button>
      ))}
    </div>
  );
}

/**
 * The tab CONTENT — reads the same shared `useMarketTab` selection the switcher (now in
 * `TopBar`) writes to, and renders the matching market's view. Only `tw` has a real view built
 * this pass — `us`/`etf` render an honest "尚未支援" placeholder rather than an empty/broken tab,
 * matching `nav-items.ts`'s own `real: false` convention for unbuilt destinations.
 */
export function MarketTabPanels({
  renderTw,
  renderUs,
  renderEtf,
}: {
  renderTw: () => ReactNode;
  renderUs?: () => ReactNode;
  renderEtf?: () => ReactNode;
}): ReactNode {
  const t = useTranslations("market.tabs");
  const { tab: active } = useMarketTab();

  return (
    <>
      {active === "tw" ? renderTw() : null}
      {active === "us" ? (renderUs?.() ?? <ComingSoon label={t("us")} />) : null}
      {active === "etf" ? (renderEtf?.() ?? <ComingSoon label={t("etf")} />) : null}
    </>
  );
}

function ComingSoon({ label }: { label: string }): ReactNode {
  const t = useTranslations("market.tabs");
  return (
    <div
      className={cn(
        "rounded-[var(--radius-card)] border border-[var(--border)] border-dashed",
        "bg-[var(--surface-2)] p-6 text-[13px] text-[var(--text-muted)]",
      )}
    >
      {t("comingSoon", { tab: label })}
    </div>
  );
}
