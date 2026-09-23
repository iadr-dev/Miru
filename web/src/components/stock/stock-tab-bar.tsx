"use client";

import { ChartLineIcon } from "@phosphor-icons/react/dist/ssr";
import { motion } from "motion/react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback } from "react";
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
import { usePathname, useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

// The 9 tabs `docs/web/stock-detail.md` §2 specs for a TW STOCK, plus 追蹤 (not in that doc —
// added per a later reference-app-driven request; see `tracker-panel.tsx`'s own docstring for
// the discrepancy with `docs/web/stock-detail/基本資料.md`'s smaller `EtfHoldersCard` plan).
// 基本資料/財務分析/籌碼分析/技術分析/相關新聞/研究圖表/追蹤/AI have real backing — the rest render
// a calm "尚未支援" state via `ComingSoon` rather than being hidden, so the roadmap stays visible
// without faking content for tabs whose backend (company-relationships) doesn't exist yet.
// "ai" was the never-built "summary" placeholder from `docs/web/stock-detail/智能摘要.md`, renamed
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

function isStockTab(value: string | null): value is StockTab {
  return (STOCK_TABS as readonly string[]).includes(value ?? "");
}

/** The active tab lives in `?tab=` rather than local `useState` — a locale switch
 * (`LanguageSwitcher`'s `router.replace(pathname, { locale })`) is a real navigation that
 * remounts this whole page tree, which would silently reset a plain `useState` back to
 * `DEFAULT_TAB` every time a user switched language mid-tab. Reading from the URL means the
 * active tab survives that navigation (and is bookmarkable/shareable) for free. */
export function StockTabBar({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.tabs");
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const rawTab = searchParams.get("tab");
  const tab: StockTab = isStockTab(rawTab) ? rawTab : DEFAULT_TAB;

  const setTab = useCallback(
    (next: StockTab) => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === DEFAULT_TAB) {
        params.delete("tab");
      } else {
        params.set("tab", next);
      }
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

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

      {tab === "basicInfo" ? <BasicInfoPanel symbol={symbol} market={market} /> : null}
      {tab === "industry" ? <IndustryPanel symbol={symbol} market={market} /> : null}
      {tab === "financials" ? <FinancialsPanel symbol={symbol} market={market} /> : null}
      {tab === "chipAnalysis" ? <ChipAnalysisPanel symbol={symbol} market={market} /> : null}
      {tab === "technical" ? <TechnicalAnalysisPanel symbol={symbol} market={market} /> : null}
      {tab === "tracker" ? <TrackerPanel symbol={symbol} market={market} /> : null}
      {tab === "ai" ? <AiPanel symbol={symbol} market={market} /> : null}
      {tab === "news" ? <StockNewsPanel symbol={symbol} market={market} /> : null}
      {tab === "research" ? <ResearchChartsPanel symbol={symbol} market={market} /> : null}
      {tab === "realtime" ? <RealtimeTab symbol={symbol} market={market} /> : null}
      {!BUILT_TABS.has(tab) ? (
        <ComingSoon icon={ChartLineIcon} title={t(tab)} body={t("comingSoonBody")} />
      ) : null}
    </div>
  );
}
