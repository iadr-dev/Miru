"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { SCREENER_MARKETS, type ScreenerMarket } from "@/types/screener";

export type ScreenerMode = "screen" | "themes";

function TabPill({
  active,
  onClick,
  layoutId,
  children,
}: {
  readonly active: boolean;
  readonly onClick: () => void;
  readonly layoutId?: string;
  readonly children: ReactNode;
}): ReactNode {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "relative min-h-11 rounded-[var(--radius-pill)] px-4 font-medium text-sm transition-colors",
        active
          ? "bg-[var(--accent-weak)] text-[var(--text)]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {children}
      {active && layoutId ? (
        <motion.span
          layoutId={layoutId}
          className="absolute inset-x-2 -bottom-px h-0.5 rounded-full bg-[var(--accent)]"
          transition={{ type: "spring", stiffness: 220, damping: 24 }}
        />
      ) : null}
    </button>
  );
}

/** Row-1 market tabs (TW/US/ETF) + Row-2 mode (篩選/主題), a pill-row `role="tablist"` matching
 * `watchlist-rail.tsx`'s tab pattern — shared-layout underline glide via `layoutId`, matching
 * `stock-tab-bar.tsx`'s active-tab indicator. Row-1 only renders in 篩選 Screen mode — 主題
 * Themes is TW-only (`ThemeCards` hardcodes `market="TW"`, no US/ETF theme data exists), so a
 * market switch has nothing to do there and the row would be dead chrome. */
export function MarketTabs({
  market,
  onMarketChange,
  mode,
  onModeChange,
}: {
  readonly market: ScreenerMarket;
  readonly onMarketChange: (next: ScreenerMarket) => void;
  readonly mode: ScreenerMode;
  readonly onModeChange: (next: ScreenerMode) => void;
}): ReactNode {
  const t = useTranslations("screener");

  return (
    <div className="flex flex-wrap items-center gap-3 border-[var(--border)] border-b p-3">
      {mode === "screen" ? (
        <div role="tablist" aria-label={t("marketTab.tw")} className="flex gap-1">
          {SCREENER_MARKETS.map((key) => (
            <TabPill
              key={key}
              active={key === market}
              onClick={() => onMarketChange(key)}
              layoutId="screener-market-underline"
            >
              {t(`marketTab.${key.toLowerCase()}`)}
            </TabPill>
          ))}
        </div>
      ) : null}

      <div role="tablist" aria-label={t("modeTab.screen")} className="ml-auto flex gap-1">
        <TabPill active={mode === "screen"} onClick={() => onModeChange("screen")}>
          {t("modeTab.screen")}
        </TabPill>
        <TabPill active={mode === "themes"} onClick={() => onModeChange("themes")}>
          {t("modeTab.themes")}
        </TabPill>
      </div>
    </div>
  );
}
