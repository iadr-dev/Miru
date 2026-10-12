"use client";

import { ChartLineIcon } from "@phosphor-icons/react/dist/ssr";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { ComingSoon } from "@/components/shell/coming-soon";
import { AiPanel } from "@/components/stock/ai-panel";
import { BasicInfoPanel } from "@/components/stock/basic-info-panel";
import { ChipAnalysisPanel } from "@/components/stock/chip-analysis-panel";
import { FinancialsPanel } from "@/components/stock/financials-panel";
import { IndustryPanel } from "@/components/stock/industry-panel";
import { RealtimeTab } from "@/components/stock/realtime-panel";
import { ResearchChartsPanel } from "@/components/stock/research-charts-panel";
import { StockNewsPanel } from "@/components/stock/stock-news-panel";
import { TechnicalAnalysisPanel } from "@/components/stock/technical-analysis-panel";
import { TrackerPanel } from "@/components/stock/tracker-panel";
import { SectionBoundary } from "@/components/ui/panel-error-boundary";
import { useDetailTab } from "@/hooks/use-detail-tab";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

// The 9 tabs for a TW STOCK, plus 追蹤 (added per a later reference-app-driven request; see
// `tracker-panel.tsx`'s own docstring).
// 基本資料/財務分析/籌碼分析/技術分析/相關新聞/研究圖表/追蹤/AI have real backing — the rest render
// a calm "尚未支援" state via `ComingSoon` rather than being hidden, so the roadmap stays visible
// without faking content for tabs whose backend (company-relationships) doesn't exist yet.
// "ai" was the never-built "summary" placeholder, renamed
// and built out as `ai-panel.tsx` (趨勢評分 + 多空評分 + AI SWOT summary).
const STOCK_TABS = [
  "basicInfo",
  "industry",
  "financials",
  "chipAnalysis",
  "technical",
  "tracker",
  "ai",
  "news",
  "research",
  "realtime",
] as const;
type StockTab = (typeof STOCK_TABS)[number];
const DEFAULT_TAB: StockTab = "basicInfo";
const BUILT_TABS: ReadonlySet<StockTab> = new Set([
  "basicInfo",
  "industry",
  "financials",
  "chipAnalysis",
  "technical",
  "tracker",
  "ai",
  "news",
  "research",
  "realtime",
]);

/** The active tab lives in `?tab=` rather than local `useState`, via the shared `useDetailTab`
 * hook (`hooks/use-detail-tab.ts`) — see that hook's own docstring for why (a locale switch is a
 * real navigation that remounts this page tree, which would reset a plain `useState`). */
export function StockTabBar({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.tabs");
  const { tab, setTab } = useDetailTab(STOCK_TABS, DEFAULT_TAB);
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
        {STOCK_TABS.map((key) => {
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
                  layoutId="stock-tab-underline"
                  className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-[var(--accent)]"
                  transition={{ type: "spring", stiffness: 220, damping: 24 }}
                />
              ) : null}
            </button>
          );
        })}
      </div>

      <SectionBoundary resetKeys={[tab, symbol]}>
        {tab === "basicInfo" ? <BasicInfoPanel symbol={symbol} market={market} /> : null}
        {tab === "industry" ? <IndustryPanel symbol={symbol} market={market} /> : null}
        {tab === "financials" ? <FinancialsPanel symbol={symbol} market={market} /> : null}
        {tab === "chipAnalysis" ? <ChipAnalysisPanel symbol={symbol} market={market} /> : null}
        {visitedTechnical ? (
          <div className={tab === "technical" ? undefined : "hidden"}>
            <TechnicalAnalysisPanel symbol={symbol} market={market} />
          </div>
        ) : null}
        {tab === "tracker" ? <TrackerPanel symbol={symbol} market={market} /> : null}
        {tab === "ai" ? <AiPanel symbol={symbol} market={market} /> : null}
        {tab === "news" ? <StockNewsPanel symbol={symbol} market={market} /> : null}
        {tab === "research" ? <ResearchChartsPanel symbol={symbol} market={market} /> : null}
        {tab === "realtime" ? <RealtimeTab symbol={symbol} market={market} /> : null}
        {!BUILT_TABS.has(tab) ? (
          <ComingSoon icon={ChartLineIcon} title={t(tab)} body={t("comingSoonBody")} />
        ) : null}
      </SectionBoundary>
    </div>
  );
}
