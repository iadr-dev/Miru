"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { IndustryMoneyFlowCard } from "@/components/market/industry-money-flow-card";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { SectorHeatmapPanel } from "@/components/market/sector-heatmap-panel";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { getIndustryFlow } from "@/lib/api/market";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

/**
 * 熱力圖 — the TW tab's 4th sub-tab: 產業熱力圖 (public sector-heatmap treemap, its own card
 * since it owns the `<Caveat>` + selection state) and 產業鏈資金流向 (Sponsor-tier ranked list).
 * 板塊輪動 (sector rotation) and per-stock 漲跌 drill-down from the approved design mockup are
 * deliberately NOT rendered here — neither has a backing aggregation yet (no whole-market
 * institutional-flow-by-industry-over-time read, no sector-constituent-roster endpoint), and
 * this codebase's convention for "not yet built" is to omit the surface entirely rather than
 * ship an empty placeholder card (`docs/web/market.md`'s own Row-2 spec lists further TW sub-tabs
 * — 產業/新聞/名人 — that are absent from `TW_SUB_TABS` for the same reason).
 */
export function HeatmapPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.industryFlow");

  const industryFlowQuery = useQuery({
    queryKey: ["industry_money_flow", market],
    queryFn: () => getIndustryFlow(market),
  });

  return (
    <div className="flex flex-col gap-4">
      <RainbowBorderCard>
        <SectorHeatmapPanel market={market} />
      </RainbowBorderCard>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <RainbowBorderCard className="flex h-full">
          <QueryState query={industryFlowQuery} skeleton={<CardSkeleton height={420} />}>
            {(data) => {
              if (isMaskedTeaser(data)) {
                return (
                  <SponsorTeaser
                    headline={t("title")}
                    preview={data.preview}
                    requiredTier={data.required_tier}
                  >
                    <div className="h-64 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
                  </SponsorTeaser>
                );
              }
              return (
                <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
                  <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">
                    {t("title")}
                  </h2>
                  <IndustryMoneyFlowCard rows={data.rows} />
                </section>
              );
            }}
          </QueryState>
        </RainbowBorderCard>
      </div>
    </div>
  );
}
