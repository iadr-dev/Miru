"use client";

import { ChartLineIcon } from "@phosphor-icons/react/dist/ssr";
import { motion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { ComingSoon } from "@/components/shell/coming-soon";
import { BasicInfoPanel } from "@/components/stock/basic-info-panel";
import { ChipAnalysisPanel } from "@/components/stock/chip-analysis-panel";
import { FinancialsPanel } from "@/components/stock/financials-panel";
import { ResearchChartsPanel } from "@/components/stock/research-charts-panel";
import { StockNewsPanel } from "@/components/stock/stock-news-panel";
import { TechnicalAnalysisPanel } from "@/components/stock/technical-analysis-panel";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

// The 9 tabs `docs/web/stock-detail.md` §2 specs for a TW STOCK. 基本資料/財務分析/籌碼分析/
// 技術分析/相關新聞/研究圖表 have real backing — the rest render a calm "尚未支援" state via
// `ComingSoon` rather than being hidden, so the roadmap stays visible without faking content for
// tabs whose backend (AI-summary/SWOT, company-relationships) doesn't exist yet.
const STOCK_TABS = [
  "basicInfo",
  "industry",
  "financials",
  "chipAnalysis",
  "technical",
  "summary",
  "news",
  "research",
  "realtime",
] as const;
type StockTab = (typeof STOCK_TABS)[number];
const BUILT_TABS: ReadonlySet<StockTab> = new Set([
  "basicInfo",
  "financials",
  "chipAnalysis",
  "technical",
  "news",
  "research",
]);

export function StockTabBar({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.tabs");
  const [tab, setTab] = useState<StockTab>("basicInfo");

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
              {key === "realtime" ? (
                <span className="text-[10px] text-[var(--text-muted)]">🔒</span>
              ) : null}
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

      {tab === "basicInfo" ? <BasicInfoPanel symbol={symbol} market={market} /> : null}
      {tab === "financials" ? <FinancialsPanel symbol={symbol} market={market} /> : null}
      {tab === "chipAnalysis" ? <ChipAnalysisPanel symbol={symbol} market={market} /> : null}
      {tab === "technical" ? <TechnicalAnalysisPanel symbol={symbol} market={market} /> : null}
      {tab === "news" ? <StockNewsPanel symbol={symbol} market={market} /> : null}
      {tab === "research" ? <ResearchChartsPanel symbol={symbol} market={market} /> : null}
      {!BUILT_TABS.has(tab) ? (
        <ComingSoon icon={ChartLineIcon} title={t(tab)} body={t("comingSoonBody")} />
      ) : null}
    </div>
  );
}
