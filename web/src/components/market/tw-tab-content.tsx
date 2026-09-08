"use client";

import { ScalesIcon, ShieldWarningIcon, SparkleIcon, SquaresFourIcon } from "@phosphor-icons/react";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { ChipDataPanel } from "@/components/market/chip-data-panel";
import { DailyFocusPanel } from "@/components/market/daily-focus-panel";
import { DispositionBoardPanel } from "@/components/market/disposition-board-panel";
import { HeatmapPanel } from "@/components/market/heatmap-panel";
import { MarketIndexStrip } from "@/components/market/market-index-strip";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

const TW_SUB_TABS = [
  { key: "focus", icon: SparkleIcon },
  { key: "chip", icon: ScalesIcon },
  { key: "disposition", icon: ShieldWarningIcon },
  { key: "heatmap", icon: SquaresFourIcon },
] as const;
type TwSubTab = (typeof TW_SUB_TABS)[number]["key"];

/**
 * The TW tab's own sub-tabs — 每日焦點 (AI 多空排行/漲跌家數+指數貢獻/漲跌幅排行, the default),
 * 籌碼 (三大法人買賣超/融資維持率/大戶加碼股), 處置 (處置王-style board: 風險股/處置中/
 * 即將出關), and 熱力圖 (產業熱力圖 treemap + 產業鏈資金流向). `docs/web/market.md`'s own Row-2
 * view-tab spec lists 2 more (產業/新聞/名人) that don't have a built panel yet, matching
 * `EtfTabContent`'s exact underline-tab shell/motion pattern so the two market tabs share one
 * sub-tab convention. Local `useState`, not the shared `useMarketTab` store: nothing outside this
 * subtree needs to know which TW sub-tab is active.
 */
export function TwTabContent({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.tabs.twSub");
  const [subTab, setSubTab] = useState<TwSubTab>("focus");

  return (
    <div className="flex flex-col gap-4">
      <MarketIndexStrip />

      <div
        role="tablist"
        aria-label={t("label")}
        className="flex w-fit gap-6 border-[var(--border)] border-b"
      >
        {TW_SUB_TABS.map(({ key, icon: Icon }) => {
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
                  layoutId="tw-sub-tab-underline"
                  className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-[var(--accent)]"
                  transition={{ type: "spring", stiffness: 220, damping: 24 }}
                />
              ) : null}
            </button>
          );
        })}
      </div>

      {subTab === "focus" ? <DailyFocusPanel market={market} /> : null}
      {subTab === "chip" ? <ChipDataPanel market={market} /> : null}
      {subTab === "disposition" ? <DispositionBoardPanel market={market} /> : null}
      {subTab === "heatmap" ? <HeatmapPanel market={market} /> : null}
    </div>
  );
}
