"use client";

import { motion } from "motion/react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { OverviewPanel } from "@/components/etf/overview-panel";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { SectionBoundary } from "@/components/ui/panel-error-boundary";
import { useDetailTab } from "@/hooks/use-detail-tab";
import { cn } from "@/lib/cn";
import type { SymbolType } from "@/types/market/shared";
import type { Market } from "@/types/settings";

// Only the panel of the open tab is needed, and the heavy ones (charts, tables) are large:
// they load when their tab is first opened instead of with every visit to the page.
const HoldingsPanel = dynamic(
  () => import("@/components/etf/holdings-panel").then((m) => m.HoldingsPanel),
  { loading: () => <CardSkeleton height={420} /> },
);
const PremiumDiscountPanel = dynamic(
  () => import("@/components/etf/premium-discount-panel").then((m) => m.PremiumDiscountPanel),
  { loading: () => <CardSkeleton height={420} /> },
);
const ActiveReportPanel = dynamic(
  () => import("@/components/etf/active-report-panel").then((m) => m.ActiveReportPanel),
  { loading: () => <CardSkeleton height={420} /> },
);
const AiPanel = dynamic(() => import("@/components/stock/ai-panel").then((m) => m.AiPanel), {
  loading: () => <CardSkeleton height={420} />,
});
const ChipAnalysisPanel = dynamic(
  () => import("@/components/stock/chip-analysis-panel").then((m) => m.ChipAnalysisPanel),
  { loading: () => <CardSkeleton height={520} /> },
);
const RealtimeTab = dynamic(
  () => import("@/components/stock/realtime-panel").then((m) => m.RealtimeTab),
  { loading: () => <CardSkeleton height={520} /> },
);
const StockNewsPanel = dynamic(
  () => import("@/components/stock/stock-news-panel").then((m) => m.StockNewsPanel),
  { loading: () => <CardSkeleton height={420} /> },
);
const TechnicalAnalysisPanel = dynamic(
  () => import("@/components/stock/technical-analysis-panel").then((m) => m.TechnicalAnalysisPanel),
  { loading: () => <CardSkeleton height={640} /> },
);

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

      <SectionBoundary resetKeys={[tab, symbol]}>
        {tab === "overview" ? <OverviewPanel symbol={symbol} market={market} /> : null}
        {tab === "realtime" ? <RealtimeTab symbol={symbol} market={market} /> : null}
        {tab === "activeReport" ? <ActiveReportPanel symbol={symbol} market={market} /> : null}
        {tab === "holdings" ? <HoldingsPanel symbol={symbol} market={market} /> : null}
        {tab === "premiumDiscount" ? (
          <PremiumDiscountPanel symbol={symbol} market={market} />
        ) : null}
        {tab === "chipAnalysis" ? <ChipAnalysisPanel symbol={symbol} market={market} /> : null}
        {visitedTechnical ? (
          <div className={tab === "technical" ? undefined : "hidden"}>
            <TechnicalAnalysisPanel symbol={symbol} market={market} />
          </div>
        ) : null}
        {tab === "ai" ? <AiPanel symbol={symbol} market={market} symbolType={symbolType} /> : null}
        {tab === "news" ? <StockNewsPanel symbol={symbol} market={market} /> : null}
      </SectionBoundary>
    </div>
  );
}
