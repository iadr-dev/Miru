"use client";

import { motion } from "motion/react";
import dynamic from "next/dynamic";
import { useTranslations } from "next-intl";
import { type ReactNode, useEffect, useState } from "react";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { SectionBoundary } from "@/components/ui/panel-error-boundary";
import { UsStockOverviewPanel } from "@/components/us-stock/overview-panel";
import { useDetailTab } from "@/hooks/use-detail-tab";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

// Only the panel of the open tab is needed, and the heavy ones (charts, tables) are large:
// they load when their tab is first opened instead of with every visit to the page.
const AiPanel = dynamic(() => import("@/components/stock/ai-panel").then((m) => m.AiPanel), {
  loading: () => <CardSkeleton height={420} />,
});
const FinancialsPanel = dynamic(
  () => import("@/components/stock/financials-panel").then((m) => m.FinancialsPanel),
  { loading: () => <CardSkeleton height={520} /> },
);
const TechnicalAnalysisPanel = dynamic(
  () => import("@/components/stock/technical-analysis-panel").then((m) => m.TechnicalAnalysisPanel),
  { loading: () => <CardSkeleton height={640} /> },
);
const NewsFilingsPanel = dynamic(
  () => import("@/components/us-stock/news-filings-panel").then((m) => m.NewsFilingsPanel),
  { loading: () => <CardSkeleton height={420} /> },
);
const SectorPeersPanel = dynamic(
  () => import("@/components/us-stock/sector-peers-panel").then((m) => m.SectorPeersPanel),
  { loading: () => <CardSkeleton height={420} /> },
);

// 6 tabs, deliberately not a stripped-down TW clone — leaning into what yfinance genuinely
// offers for US names: a company-profile snapshot (now folded in with analyst research — see
// below), full financials (annual/quarterly/TTM), the main technical chart, new Sector/Industry
// peer data (no TW precedent), a general news+SEC-filings feed (replacing TW's FinMind-news+PTT
// pairing, both TW-only sources), and AI factor scoring — reused verbatim from the stock/ETF
// pages, same tool, same graceful degradation for factors with no US source data yet. 即時
// (realtime, via yfinance's WebSocket streaming class) is deliberately deferred — it needs a new
// streaming-client architecture, not just a data-availability gap.
//
// There is no standalone 研究 tab here (unlike TW/ETF): `ResearchChartsPanel`'s other two cards
// (估值河流圖/個股事件) are TW-only-gated, so for a US symbol that tab rendered only
// `AnalystConsensusCard` — folded into 總覽 instead (`UsStockOverviewPanel`) rather than keeping
// a tab that was nearly empty.
const US_STOCK_TABS = [
  "overview",
  "financials",
  "technical",
  "sectorPeers",
  "newsFilings",
  "ai",
] as const;
type UsStockTab = (typeof US_STOCK_TABS)[number];
const DEFAULT_TAB: UsStockTab = "overview";

/** US-stock-detail tab bar — same `?tab=`-in-URL pattern `StockTabBar`/`EtfTabBar` use, via the
 * shared `useDetailTab` hook. `financials`/`technical`/`ai` are the exact same components the TW
 * stock page uses (each already accepts `market` and internally hides/adapts TW-only
 * sub-content); `overview`/`sectorPeers`/`newsFilings` are new, US-specific panels. */
export function UsStockTabBar({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.tabs");
  const { tab, setTab } = useDetailTab(US_STOCK_TABS, DEFAULT_TAB);
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
        {US_STOCK_TABS.map((key) => {
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
                  layoutId="us-stock-tab-underline"
                  className="absolute inset-x-0 -bottom-px h-0.5 rounded-full bg-[var(--accent)]"
                  transition={{ type: "spring", stiffness: 220, damping: 24 }}
                />
              ) : null}
            </button>
          );
        })}
      </div>

      <SectionBoundary resetKeys={[tab, symbol]}>
        {tab === "overview" ? <UsStockOverviewPanel symbol={symbol} market={market} /> : null}
        {tab === "financials" ? <FinancialsPanel symbol={symbol} market={market} /> : null}
        {visitedTechnical ? (
          <div className={tab === "technical" ? undefined : "hidden"}>
            <TechnicalAnalysisPanel symbol={symbol} market={market} />
          </div>
        ) : null}
        {tab === "sectorPeers" ? <SectorPeersPanel symbol={symbol} market={market} /> : null}
        {tab === "newsFilings" ? <NewsFilingsPanel symbol={symbol} market={market} /> : null}
        {tab === "ai" ? <AiPanel symbol={symbol} market={market} /> : null}
      </SectionBoundary>
    </div>
  );
}
