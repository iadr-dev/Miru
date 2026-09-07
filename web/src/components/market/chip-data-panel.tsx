"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { HolderAccumulationCard } from "@/components/market/holder-accumulation-card";
import { InsiderTransferDeclarationPanel } from "@/components/market/insider-transfer-declaration-panel";
import { InstitutionalFlowCard } from "@/components/market/institutional-flow-card";
import { MarginMaintenanceCard } from "@/components/market/margin-maintenance-card";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import {
  getHolderAccumulation,
  getInstitutionalRanking,
  getMarginMaintenance,
  getMarginShortBalance,
} from "@/lib/api/market";
import type { HolderThreshold, HolderWindow } from "@/types/market";
import { isMaskedTeaser } from "@/types/market";
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
 * 籌碼 — the TW tab's chip-data sub-tab: 三大法人買賣超, 融資維持率 (+ 融資券餘額), and
 * 大戶加碼股. These three cards previously rendered directly in `MarketPanel`'s `renderTw`
 * (the margin-maintenance card lived in the sibling `TopSplit`) — relocated here as the TW tab
 * grows real sub-tabs (`docs/web/market.md`'s Row-2 view-tab spec: 每日焦點/熱力圖/籌碼/產業/
 * 新聞/處置/名人), mirroring `EtfTabContent`'s own per-sub-tab panel split.
 */
export function ChipDataPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards");
  const [holderThreshold, setHolderThreshold] = useState<HolderThreshold>(400);
  const [holderWindow, setHolderWindow] = useState<HolderWindow>(2);
  const flowWindow = institutionalFlowWindow();
  const marginWindow = oneYearWindow();

  const institutionalQuery = useQuery({
    queryKey: ["institutional_flow_total_market", market, flowWindow.startDate, flowWindow.endDate],
    queryFn: () => getInstitutionalRanking(market, flowWindow),
  });

  const marginQuery = useQuery({
    queryKey: ["margin_maintenance_ratio", market, marginWindow.startDate, marginWindow.endDate],
    queryFn: () => getMarginMaintenance(market, marginWindow),
  });

  const marginShortBalanceQuery = useQuery({
    queryKey: ["margin_short_total_market", market],
    queryFn: () => getMarginShortBalance(market),
  });

  const holderAccumulationQuery = useQuery({
    queryKey: ["holder_accumulation", market, holderThreshold, holderWindow],
    queryFn: () =>
      getHolderAccumulation(market, holderThreshold, holderWindow, undefined, MAX_TOP_N),
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

        {/* Fills the grid cell to 大戶加碼股's right — the grid has 3 cards on a 2-col layout,
            leaving that cell empty otherwise. */}
        <RainbowBorderCard className="flex h-full">
          <InsiderTransferDeclarationPanel market={market} />
        </RainbowBorderCard>
      </div>
    </div>
  );
}
