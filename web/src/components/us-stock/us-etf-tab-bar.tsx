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
const TechnicalAnalysisPanel = dynamic(
  () => import("@/components/stock/technical-analysis-panel").then((m) => m.TechnicalAnalysisPanel),
  { loading: () => <CardSkeleton height={640} /> },
);
const FundHoldingsPanel = dynamic(
  () => import("@/components/us-stock/fund-holdings-panel").then((m) => m.FundHoldingsPanel),
  { loading: () => <CardSkeleton height={420} /> },
);
const NewsFilingsPanel = dynamic(
  () => import("@/components/us-stock/news-filings-panel").then((m) => m.NewsFilingsPanel),
  { loading: () => <CardSkeleton height={420} /> },
);

// 5 tabs, not a copy of `UsStockTabBar`'s 6: 財務/產業同業 don't apply to a fund (no income
// statement, no "peer companies" concept — it doesn't compete against other funds the way a
// stock competes against peers), replaced by 持股與產業 (`get_fund_holdings` — real top-holdings
// + sector-weighting data, the fund equivalent of what a stock's 財務/產業同業 tabs would show).
// 總覽/技術/新聞公告/AI are reused verbatim from `UsStockTabBar` — none of those four assume
// "this is a plain equity", so nothing about them needs a fund-specific fork.
const US_ETF_TABS = ["overview", "holdings", "technical", "newsFilings", "ai"] as const;
type UsEtfTab = (typeof US_ETF_TABS)[number];
const DEFAULT_TAB: UsEtfTab = "overview";

/** US-ETF-detail tab bar — same `?tab=`-in-URL pattern every other detail tab bar uses. Rendered
 * by `page.tsx` once its own `useIsUsFund` check resolves a US symbol as a fund (`classifySymbolType`
 * can't tell US stocks from US ETFs by code shape — see that hook's own docstring). */
export function UsEtfTabBar({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.etfTabs");
  const { tab, setTab } = useDetailTab(US_ETF_TABS, DEFAULT_TAB);
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
        {US_ETF_TABS.map((key) => {
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
                  layoutId="us-etf-tab-underline"
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
        {tab === "holdings" ? <FundHoldingsPanel symbol={symbol} market={market} /> : null}
        {visitedTechnical ? (
          <div className={tab === "technical" ? undefined : "hidden"}>
            <TechnicalAnalysisPanel symbol={symbol} market={market} />
          </div>
        ) : null}
        {tab === "newsFilings" ? <NewsFilingsPanel symbol={symbol} market={market} /> : null}
        {tab === "ai" ? <AiPanel symbol={symbol} market={market} /> : null}
      </SectionBoundary>
    </div>
  );
}
