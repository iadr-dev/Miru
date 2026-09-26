"use client";

import { NewspaperIcon, SparkleIcon, SquaresFourIcon } from "@phosphor-icons/react";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { DailyFocusPanel } from "@/components/market/daily-focus-panel";
import { HeatmapPanel } from "@/components/market/heatmap-panel";
import { NewsFeedPanel } from "@/components/market/news-feed-panel";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

const US_SUB_TABS = [
  { key: "focus", icon: SparkleIcon },
  { key: "heatmap", icon: SquaresFourIcon },
  { key: "news", icon: NewspaperIcon },
] as const;
type UsSubTab = (typeof US_SUB_TABS)[number]["key"];

/**
 * The US tab's own sub-tabs — mirrors `TwTabContent`'s shell/motion pattern exactly, but only 3
 * keys: 每日焦點 (AI 多空排行/漲跌家數/漲跌幅排行, the default), 熱力圖 (產業熱力圖/股票熱力圖,
 * no 板塊輪動 — see `SectorHeatmapPanel`'s own market-aware view list), and 新聞. `TwTabContent`'s
 * 籌碼/處置/PTT sub-tabs have no US backing data at all (margin/holder-tier/disposition-board
 * are TWSE/TPEx-specific regulatory concepts; PTT has no US equivalent) — dropped entirely
 * rather than rendered as an empty/broken tab. No `MarketIndexStrip` either: that component
 * takes no props and is hardcoded to TW indices (加權指數/櫃買指數) plus Sponsor-tier
 * semiconductor/TAIEX-futures/VIX items — there is no real US index ingested to show in its
 * place (only the SPY ETF proxy exists, not a genuine index dataset).
 */
export function UsTabContent({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.tabs.usSub");
  const [subTab, setSubTab] = useState<UsSubTab>("focus");

  return (
    <div className="flex flex-col gap-4">
      <div
        role="tablist"
        aria-label={t("label")}
        className="flex w-fit gap-6 border-[var(--border)] border-b"
      >
        {US_SUB_TABS.map(({ key, icon: Icon }) => {
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
                  layoutId="us-sub-tab-underline"
                  className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-[var(--accent)]"
                  transition={{ type: "spring", stiffness: 220, damping: 24 }}
                />
              ) : null}
            </button>
          );
        })}
      </div>

      {subTab === "focus" ? <DailyFocusPanel market={market} /> : null}
      {subTab === "heatmap" ? <HeatmapPanel market={market} /> : null}
      {subTab === "news" ? <NewsFeedPanel market={market} /> : null}
    </div>
  );
}
