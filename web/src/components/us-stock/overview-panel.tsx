"use client";

import type { ReactNode } from "react";
import { EsgCard, ProfileCard } from "@/components/stock/basic-info-panel";
import { AnalystConsensusCard } from "@/components/stock/research-charts-panel";
import { UsMarketCalendarCard } from "@/components/us-stock/calendar-events-card";
import type { Market } from "@/types/settings";

/** 總覽 tab — reuses `basic-info-panel.tsx`'s `ProfileCard`/`EsgCard` verbatim rather than
 * forking them: both already degrade gracefully for a market with no MOPS equivalent
 * (`get_company_profile`'s own `missing_fields` contract) or are genuinely market-generic
 * (`Capability.ESG`, yfinance, real for every market). Price/quote is already covered by
 * `AssetHeader` above this tab bar — this tab is the company-profile snapshot, not a second
 * price display.
 *
 * `AnalystConsensusCard` (also reused verbatim, from `research-charts-panel.tsx`) is folded in
 * here rather than kept behind its own 研究 tab: that tab's other two cards are TW-only-gated,
 * so for a US symbol it rendered only this one card — there is no US 研究 tab anymore. */
export function UsStockOverviewPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <ProfileCard symbol={symbol} market={market} />
      <EsgCard symbol={symbol} market={market} />
      <AnalystConsensusCard symbol={symbol} market={market} />
      <UsMarketCalendarCard symbol={symbol} market={market} />
    </div>
  );
}
