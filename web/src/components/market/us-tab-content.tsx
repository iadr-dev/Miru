"use client";

import {
  ChartLineUpIcon,
  NewspaperIcon,
  SparkleIcon,
  SquaresFourIcon,
} from "@phosphor-icons/react";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { DailyFocusPanel } from "@/components/market/daily-focus-panel";
import { HeatmapPanel } from "@/components/market/heatmap-panel";
import { MacroPanel } from "@/components/market/macro-panel";
import { NewsFeedPanel } from "@/components/market/news-feed-panel";
import { UsMarketIndexStrip } from "@/components/market/us-market-index-strip";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

const US_SUB_TABS = [
  { key: "focus", icon: SparkleIcon },
  { key: "heatmap", icon: SquaresFourIcon },
  { key: "news", icon: NewspaperIcon },
  { key: "macro", icon: ChartLineUpIcon },
] as const;
type UsSubTab = (typeof US_SUB_TABS)[number]["key"];

/**
 * The US tab's own sub-tabs — mirrors `TwTabContent`'s shell/motion pattern exactly, with 4
 * keys: 每日焦點 (AI 多空排行/漲跌家數/排行榜, the default), 熱力圖 (產業熱力圖/股票熱力圖, no
 * 板塊輪動 — see `SectorHeatmapPanel`'s own market-aware view list), 新聞, and 總經 (macro-
 * economic release calendar + yield/Fed-rate/gold/oil indicators, `MacroPanel`).
 * `TwTabContent`'s 籌碼/處置/PTT sub-tabs have no US backing data at all (margin/holder-tier/
 * disposition-board are TWSE/TPEx-specific regulatory concepts; PTT has no US equivalent) —
 * dropped entirely rather than rendered as an empty/broken tab. `UsMarketIndexStrip` (S&P500/
 * Dow Jones/Nasdaq/SOX/VIX/US 10Y yield/Fed funds rate, live yfinance index quotes confirmed
 * live this session) sits above the sub-tab bar, mirroring `TwTabContent`'s own
 * `MarketIndexStrip` placement — this is the real US counterpart that used to have no backing
 * data at all (only the SPY ETF proxy existed before).
 */
export function UsTabContent({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.tabs.usSub");
  const [subTab, setSubTab] = useState<UsSubTab>("focus");

  return (
    <div className="flex flex-col gap-4">
      <UsMarketIndexStrip />

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
      {subTab === "macro" ? <MacroPanel /> : null}
    </div>
  );
}
