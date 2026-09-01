"use client";

import { ChartLineUpIcon, ListIcon } from "@phosphor-icons/react";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { EtfLeaderboardPanel } from "@/components/market/etf-leaderboard-panel";
import { EtfListPanel } from "@/components/market/etf-list-panel";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

const ETF_SUB_TABS = [
  { key: "leaderboard", icon: ChartLineUpIcon },
  { key: "list", icon: ListIcon },
] as const;
type EtfSubTab = (typeof ETF_SUB_TABS)[number]["key"];

/** The ETF tab's own two sub-tabs — AI 多空排行 (leaderboard, scoped to the ETF universe) and the
 * full sortable ETF list. Local `useState`, not the shared `useMarketTab` store: nothing outside
 * this subtree needs to know which ETF sub-tab is active. Underline style with a `layoutId`
 * shared-element indicator that glides between tabs on click — same spring-transition mechanism
 * `shell/sidebar.tsx`'s active-nav-edge indicator already uses, not a new motion pattern. */
export function EtfTabContent({ market }: { market: Market }): ReactNode {
  const t = useTranslations("market.tabs.etfSub");
  const [subTab, setSubTab] = useState<EtfSubTab>("leaderboard");

  return (
    <div className="flex flex-col gap-4">
      <div
        role="tablist"
        aria-label={t("label")}
        className="flex w-fit gap-6 border-[var(--border)] border-b"
      >
        {ETF_SUB_TABS.map(({ key, icon: Icon }) => {
          const active = subTab === key;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setSubTab(key)}
              className={cn(
                "relative flex min-h-11 items-center gap-1.5 px-1 text-[14px] transition-colors",
                active
                  ? "font-semibold text-[var(--text)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              <Icon
                size={16}
                weight="regular"
                aria-hidden
                className={active ? "text-[var(--accent)]" : undefined}
              />
              {t(key)}
              {active ? (
                <motion.span
                  layoutId="etf-sub-tab-underline"
                  className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-[var(--accent)]"
                  transition={{ type: "spring", stiffness: 220, damping: 24 }}
                />
              ) : null}
            </button>
          );
        })}
      </div>

      {subTab === "leaderboard" ? (
        <RainbowBorderCard className="flex h-full">
          <EtfLeaderboardPanel market={market} />
        </RainbowBorderCard>
      ) : (
        <EtfListPanel market={market} />
      )}
    </div>
  );
}
