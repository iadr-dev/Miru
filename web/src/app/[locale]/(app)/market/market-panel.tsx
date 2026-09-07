"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { BreadthOrderBookCard } from "@/components/market/breadth-order-book-card";
import { EtfTabContent } from "@/components/market/etf-tab-content";
import { HolderAccumulationCard } from "@/components/market/holder-accumulation-card";
import { InstitutionalFlowCard } from "@/components/market/institutional-flow-card";
import { MarketTabPanels } from "@/components/market/market-tabs";
import { PriceMomentumCard } from "@/components/market/price-momentum-card";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { TopSplit } from "@/components/market/top-split";
import { useDataUpdates } from "@/hooks/use-data-updates";
import {
  getAdvanceDecline,
  getHolderAccumulation,
  getIndexContribution,
  getInstitutionalRanking,
  getPriceMomentum,
  MarketApiError,
} from "@/lib/api/market";
import type {
  HolderThreshold,
  HolderWindow,
  LeaderboardDirection,
  MomentumPeriod,
} from "@/types/market";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

const DEFAULT_MARKET: Market = "TW";
// The backend caps top_n at 50 for both `/market/price-momentum` and
// `/market/holder-accumulation` — fetch the full cap so `PriceMomentumCard`/
// `HolderAccumulationCard` can show 50 via their own collapse-to-10/expand-modal pattern
// (mirrors `LeaderboardCard`'s own "Show all 50", the same limit `/analytics/rankings` caps at).
const MAX_TOP_N = 50;

/** A bounded 90-day window ending today for the institutional-flow chart's multi-day bar
 * history — long enough for a meaningful trend, never an unbounded "all history" fetch. */
function institutionalFlowWindow(): { startDate: string; endDate: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 90);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
}

export function MarketPanel(): ReactNode {
  useDataUpdates();
  const tCards = useTranslations("market.cards");
  const [momentumDirection, setMomentumDirection] = useState<LeaderboardDirection>("bullish");
  const [momentumPeriod, setMomentumPeriod] = useState<MomentumPeriod>("week");
  const [holderThreshold, setHolderThreshold] = useState<HolderThreshold>(400);
  const [holderWindow, setHolderWindow] = useState<HolderWindow>(2);
  const flowWindow = institutionalFlowWindow();

  const institutionalQuery = useQuery({
    queryKey: [
      "institutional_flow_total_market",
      DEFAULT_MARKET,
      flowWindow.startDate,
      flowWindow.endDate,
    ],
    queryFn: () => getInstitutionalRanking(DEFAULT_MARKET, flowWindow),
  });

  const breadthQuery = useQuery({
    queryKey: ["advance_decline", DEFAULT_MARKET],
    queryFn: () => getAdvanceDecline(DEFAULT_MARKET),
  });

  const indexContributionQuery = useQuery({
    queryKey: ["index_contribution", DEFAULT_MARKET],
    queryFn: () => getIndexContribution(DEFAULT_MARKET),
  });

  const priceMomentumQuery = useQuery({
    queryKey: ["price_momentum", DEFAULT_MARKET, momentumPeriod],
    queryFn: () => getPriceMomentum(DEFAULT_MARKET, momentumPeriod, undefined, MAX_TOP_N),
  });

  const holderAccumulationQuery = useQuery({
    queryKey: ["holder_accumulation", DEFAULT_MARKET, holderThreshold, holderWindow],
    queryFn: () =>
      getHolderAccumulation(DEFAULT_MARKET, holderThreshold, holderWindow, undefined, MAX_TOP_N),
  });

  return (
    <div className="p-6">
      <MarketTabPanels
        renderTw={() => (
          <div className="flex flex-col gap-4">
            <TopSplit market={DEFAULT_MARKET} />

            <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
              <QueryState query={institutionalQuery}>
                {(data) => (
                  <RainbowBorderCard>
                    <InstitutionalFlowCard rows={data.rows} />
                  </RainbowBorderCard>
                )}
              </QueryState>

              <QueryState query={breadthQuery}>
                {(breadth) => (
                  <QueryState query={indexContributionQuery}>
                    {(contribution) => (
                      <RainbowBorderCard>
                        <BreadthOrderBookCard breadth={breadth} indexContribution={contribution} />
                      </RainbowBorderCard>
                    )}
                  </QueryState>
                )}
              </QueryState>

              <QueryState query={priceMomentumQuery}>
                {(data) => (
                  <RainbowBorderCard className="flex h-full">
                    <PriceMomentumCard
                      gainers={data.top_gainers}
                      losers={data.top_losers}
                      direction={momentumDirection}
                      onDirectionChange={setMomentumDirection}
                      period={momentumPeriod}
                      onPeriodChange={setMomentumPeriod}
                      market={DEFAULT_MARKET}
                    />
                  </RainbowBorderCard>
                )}
              </QueryState>

              <QueryState query={holderAccumulationQuery}>
                {(data) => {
                  if (isMaskedTeaser(data)) {
                    return (
                      <SponsorTeaser
                        headline={tCards("holderAccumulation.title")}
                        preview={data.preview}
                        requiredTier={data.required_tier}
                      >
                        <div className="h-52 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
                      </SponsorTeaser>
                    );
                  }
                  return (
                    <RainbowBorderCard className="flex h-full">
                      <HolderAccumulationCard
                        rows={data.rows}
                        threshold={holderThreshold}
                        onThresholdChange={setHolderThreshold}
                        window={holderWindow}
                        onWindowChange={setHolderWindow}
                        asOf={data.as_of}
                        periodAgo={data.period_ago}
                      />
                    </RainbowBorderCard>
                  );
                }}
              </QueryState>
            </div>
          </div>
        )}
        renderEtf={() => <EtfTabContent market={DEFAULT_MARKET} />}
      />
    </div>
  );
}

/** Shared loading/empty/error branching for every card's query (web/CLAUDE.md §6) — a shaped
 * skeleton while loading, a calm inline error with retry on failure, and the caller's own render
 * function once data is present (which itself may still show a masked teaser or an empty state).
 *
 * `skeleton` lets each caller supply a block-shaped placeholder matching ITS OWN card's real
 * loaded height/layout (§6: "shaped shimmer skeleton always — block shapes matching final
 * layout") — the generic 2-line fallback below is deliberately short, so a caller with a taller
 * real card (a 10-row leaderboard, a chart+table) that doesn't pass one will visibly grow once
 * data lands: a layout-shift bug, not a cosmetic nit. Every call site should pass its own. */
export function QueryState<T>({
  query,
  skeleton,
  children,
}: {
  query: {
    isLoading: boolean;
    isError: boolean;
    error: unknown;
    data: T | undefined;
    refetch: () => void;
  };
  skeleton?: ReactNode;
  children: (data: T) => ReactNode;
}): ReactNode {
  const t = useTranslations("market.cards");

  if (query.isLoading) {
    return (
      skeleton ?? (
        <div className="animate-pulse space-y-2" aria-hidden>
          <div className="h-24 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
          <div className="h-4 w-2/3 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
        </div>
      )
    );
  }
  if (query.isError) {
    const message = query.error instanceof MarketApiError ? query.error.message : t("error");
    return (
      <div
        role="alert"
        className="rounded-[var(--radius-input)] border border-[var(--warn)]/40 bg-[var(--surface-2)] p-3"
      >
        <p className="text-[13px] text-[var(--text)]">{message}</p>
        <button
          type="button"
          onClick={() => query.refetch()}
          className="mt-2 font-medium text-[13px] text-[var(--accent-text)] hover:underline"
        >
          {t("retry")}
        </button>
      </div>
    );
  }
  if (query.data === undefined) {
    return null;
  }
  return <>{children(query.data)}</>;
}
