"use client";

import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { ActiveReportPanel } from "@/components/etf/active-report-panel";
import { HoldingsPanel } from "@/components/etf/holdings-panel";
import { OverviewPanel } from "@/components/etf/overview-panel";
import { PremiumDiscountPanel } from "@/components/etf/premium-discount-panel";
import { AiPanel } from "@/components/stock/ai-panel";
import { ChipAnalysisPanel } from "@/components/stock/chip-analysis-panel";
import { RealtimeTab } from "@/components/stock/realtime-panel";
import { StockNewsPanel } from "@/components/stock/stock-news-panel";
import { TechnicalAnalysisPanel } from "@/components/stock/technical-analysis-panel";
import { useDetailTab } from "@/hooks/use-detail-tab";
import { cn } from "@/lib/cn";
import type { SymbolType } from "@/types/market";
import type { Market } from "@/types/settings";

// 總覽/操作日報/成分股/折溢價 are new, ETF-only panels (`components/etf/*`); 看盤/籌碼/技術/AI/新聞
// reuse the stock-detail page's own panels verbatim (`components/stock/*`) — each already takes
// only `symbol`/`market` (plus `AiPanel`'s optional `symbolType`), no stock-only assumption baked
// in (institutional flow/margin-short/broker-chip-flow are published for ETF symbols too, not
// just plain equities). 討論/forum is deliberately not a tab — never requested, no backing.
const ETF_TABS = [
  "overview",
  "realtime",
  "activeReport",
  "holdings",
  "premiumDiscount",
  "chipAnalysis",
  "technical",
  "ai",
  "news",
] as const;
type EtfTab = (typeof ETF_TABS)[number];
const DEFAULT_TAB: EtfTab = "overview";

/** ETF-detail tab bar — same `?tab=`-in-URL pattern `StockTabBar` uses, via the shared
 * `useDetailTab` hook (`hooks/use-detail-tab.ts`), so a locale switch doesn't reset the active
 * tab here either. */
export function EtfTabBar({
  symbol,
  market,
  symbolType,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly symbolType: SymbolType;
}): ReactNode {
  const t = useTranslations("etf.tabs");
  const { tab, setTab } = useDetailTab(ETF_TABS, DEFAULT_TAB);
  // Once visited, 技術分析 stays mounted (hidden via CSS on other tabs) instead of unmounting —
  // its indicator/range/overlay toggles live in local `useState`, which switching tabs would
  // otherwise reset to defaults every time (unmount destroys that state).
  const [visitedTechnical, setVisitedTechnical] = useState(false);
  useEffect(() => {
    if (tab === "technical") setVisitedTechnical(true);
  }, [tab]);

  return (
    <div className="flex flex-col gap-4 p-5">
      <div
        role="tablist"
        aria-label={t("label")}
        className="flex w-full gap-6 overflow-x-auto border-[var(--border)] border-b"
      >
        {ETF_TABS.map((key) => {
          const active = tab === key;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(key)}
              className={cn(
                "relative flex min-h-11 shrink-0 items-center gap-1 whitespace-nowrap px-1 text-[14px] transition-colors",
                active
                  ? "font-semibold text-[var(--text)]"
                  : "text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {t(key)}
              {active ? (
                <motion.span
                  layoutId="etf-tab-underline"
                  className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-[var(--accent)]"
                  transition={{ type: "spring", stiffness: 220, damping: 24 }}
                />
              ) : null}
            </button>
          );
        })}
      </div>

      {tab === "overview" ? <OverviewPanel symbol={symbol} market={market} /> : null}
      {tab === "realtime" ? <RealtimeTab symbol={symbol} market={market} /> : null}
      {tab === "activeReport" ? <ActiveReportPanel symbol={symbol} market={market} /> : null}
      {tab === "holdings" ? <HoldingsPanel symbol={symbol} market={market} /> : null}
      {tab === "premiumDiscount" ? <PremiumDiscountPanel symbol={symbol} market={market} /> : null}
      {tab === "chipAnalysis" ? <ChipAnalysisPanel symbol={symbol} market={market} /> : null}
      {visitedTechnical ? (
        <div className={tab === "technical" ? undefined : "hidden"}>
          <TechnicalAnalysisPanel symbol={symbol} market={market} />
        </div>
      ) : null}
      {tab === "ai" ? <AiPanel symbol={symbol} market={market} symbolType={symbolType} /> : null}
      {tab === "news" ? <StockNewsPanel symbol={symbol} market={market} /> : null}
    </div>
  );
}
