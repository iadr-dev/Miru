"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { HolderAccumulationCard } from "@/components/market/holder-accumulation-card";
import { IndustryMoneyFlowCard } from "@/components/market/industry-money-flow-card";
import { InsiderTransferDeclarationPanel } from "@/components/market/insider-transfer-declaration-panel";
import { InstitutionalFlowCard } from "@/components/market/institutional-flow-card";
import { MarginMaintenanceCard } from "@/components/market/margin-maintenance-card";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { QueryState } from "@/components/ui/query-state";
import {
  getHolderAccumulation,
  getIndustryFlow,
  getInstitutionalRanking,
  getMarginMaintenance,
  getMarginShortBalance,
} from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import type { HolderThreshold, HolderWindow } from "@/types/market/rankings";
import { isMaskedTeaser } from "@/types/market/shared";
import type { Market } from "@/types/settings";

// The backend caps top_n at 50 for `/market/holder-accumulation` — fetch the full cap so
// `HolderAccumulationCard` can show 50 via its own collapse-to-10/expand-modal pattern.
const MAX_TOP_N = 50;

/** A bounded 90-day window ending today for the institutional-flow chart's multi-day bar
 * history — long enough for a meaningful trend, never an unbounded "all history" fetch. */
function institutionalFlowWindow(): { startDate: string; endDate: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 90);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
}

/** A bounded ~1-year window ending today so `MarginMaintenanceCard`'s trend rows (今日/前一交易日/
 * 一週前/一個月前/一年前) each have a real fetched row to match against — see `TopSplit`'s own
 * (identical) window helper docstring for why 385 days rather than 365. */
function oneYearWindow(): { startDate: string; endDate: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 385);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
}

/**
 * 籌碼 — the TW tab's chip-data sub-tab: 三大法人買賣超, 融資維持率 (+ 融資券餘額), 大戶加碼股,
 * and 產業鏈資金流向 (moved here from 熱力圖 — a chip/institutional-flow read, the same family
 * as this sub-tab's other cards). 內部人股權轉讓事前申報 renders below the grid as its own
 * full-width row. Mirrors `EtfTabContent`'s own per-sub-tab panel split.
 */
export function ChipDataPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards");
  const [holderThreshold, setHolderThreshold] = useState<HolderThreshold>(400);
  const [holderWindow, setHolderWindow] = useState<HolderWindow>(2);
  const flowWindow = institutionalFlowWindow();
  const marginWindow = oneYearWindow();

  const institutionalQuery = useQuery({
    queryKey: queryKeys.marketRead(
      "institutional_flow_total_market",
      market,
      flowWindow.startDate,
      flowWindow.endDate,
    ),
    queryFn: () => getInstitutionalRanking(market, flowWindow),
  });

  const marginQuery = useQuery({
    queryKey: queryKeys.marketRead(
      "margin_maintenance_ratio",
      market,
      marginWindow.startDate,
      marginWindow.endDate,
    ),
    queryFn: () => getMarginMaintenance(market, marginWindow),
  });

  const marginShortBalanceQuery = useQuery({
    queryKey: queryKeys.marketRead("margin_short_total_market", market),
    queryFn: () => getMarginShortBalance(market),
  });

  const holderAccumulationQuery = useQuery({
    queryKey: queryKeys.marketRead("holder_accumulation", market, holderThreshold, holderWindow),
    queryFn: () =>
      getHolderAccumulation(market, holderThreshold, holderWindow, undefined, MAX_TOP_N),
  });

  const industryFlowQuery = useQuery({
    queryKey: queryKeys.marketRead("industry_money_flow", market),
    queryFn: () => getIndustryFlow(market),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <RainbowBorderCard className="flex h-full">
          <QueryState query={institutionalQuery} skeleton={<CardSkeleton height={620} />}>
            {(data) => <InstitutionalFlowCard rows={data.rows} />}
          </QueryState>
        </RainbowBorderCard>

        <RainbowBorderCard className="flex h-full">
          <QueryState query={marginQuery} skeleton={<CardSkeleton height={620} />}>
            {(data) => {
              if (isMaskedTeaser(data)) {
                return (
                  <SponsorTeaser
                    headline={t("marginMaintenance.title")}
                    preview={data.preview}
                    requiredTier={data.required_tier}
                  >
                    <div className="h-52 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
                  </SponsorTeaser>
                );
              }
              return (
                <MarginMaintenanceCard
                  rows={data.rows}
                  shortBalanceRows={
                    marginShortBalanceQuery.data && !isMaskedTeaser(marginShortBalanceQuery.data)
                      ? marginShortBalanceQuery.data.rows
                      : []
                  }
                />
              );
            }}
          </QueryState>
        </RainbowBorderCard>

        <RainbowBorderCard className="flex h-full">
          <QueryState query={holderAccumulationQuery} skeleton={<CardSkeleton height={470} />}>
            {(data) => {
              if (isMaskedTeaser(data)) {
                return (
                  <SponsorTeaser
                    headline={t("holderAccumulation.title")}
                    preview={data.preview}
                    requiredTier={data.required_tier}
                  >
                    <div className="h-52 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
                  </SponsorTeaser>
                );
              }
              return (
                <HolderAccumulationCard
                  rows={data.rows}
                  threshold={holderThreshold}
                  onThresholdChange={setHolderThreshold}
                  window={holderWindow}
                  onWindowChange={setHolderWindow}
                  asOf={data.as_of}
                  periodAgo={data.period_ago}
                />
              );
            }}
          </QueryState>
        </RainbowBorderCard>

        {/* Fills the grid cell to 大戶加碼股's right — the grid has 4 cards on a 2-col layout. */}
        <RainbowBorderCard className="flex h-full">
          <QueryState query={industryFlowQuery} skeleton={<CardSkeleton height={470} />}>
            {(data) => <IndustryMoneyFlowCard rows={data.rows} />}
          </QueryState>
        </RainbowBorderCard>
      </div>

      <RainbowBorderCard>
        <InsiderTransferDeclarationPanel market={market} />
      </RainbowBorderCard>
    </div>
  );
}
