"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { BreadthOrderBookCard } from "@/components/market/breadth-order-book-card";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { EarningsCalendarPanel } from "@/components/market/earnings-calendar-panel";
import { LeaderboardCard } from "@/components/market/leaderboard-card";
import { MaterialAnnouncementFeedPanel } from "@/components/market/material-announcement-feed-panel";
import { PriceMomentumCard } from "@/components/market/price-momentum-card";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { RankingsCard } from "@/components/market/rankings-card";
import {
  getAdvanceDecline,
  getIndexContribution,
  getPriceMomentum,
  getScoreLeaderboard,
} from "@/lib/api/market";
import type { LeaderboardDirection, MomentumPeriod } from "@/types/market";
import type { Market } from "@/types/settings";

// The backend caps top_n at 50 for `/market/price-momentum` — fetch the full cap so
// `PriceMomentumCard` can show 50 via its own collapse-to-10/expand-modal pattern (mirrors
// `LeaderboardCard`'s own "Show all 50", the same limit `/analytics/rankings` caps at).
const MAX_TOP_N = 50;

/**
 * 每日焦點 — the market tab's default sub-tab: AI 多空排行 (whole-market score leaderboard),
 * 漲跌家數 (+ 指數貢獻排行 for TW only) breadth card, and 漲跌幅排行 (price momentum). These
 * cards previously rendered directly in `MarketPanel`'s `renderTw` with no sub-tab wrapper —
 * pulled out here as the TW tab grows a second sub-tab (籌碼) per `docs/web/market.md`'s own
 * Row-2 view-tab spec, mirroring `TwTabContent`'s sibling `ChipDataPanel`.
 *
 * US variant (`market === "US"`) drops two cards with no US backing data: 指數貢獻排行 (no real
 * US index is ingested — only the SPY ETF proxy exists, not a genuine index-contribution
 * dataset) and 重大訊息 (MOPS material announcements are TW-only). The grid falls back to 2
 * columns x 2 rows instead of the TW 4-card layout.
 */
export function DailyFocusPanel({ market }: { readonly market: Market }): ReactNode {
  const [momentumDirection, setMomentumDirection] = useState<LeaderboardDirection>("bullish");
  const [momentumPeriod, setMomentumPeriod] = useState<MomentumPeriod>("week");
  const [leaderboardDirection, setLeaderboardDirection] = useState<LeaderboardDirection>("bullish");
  const isUs = market === "US";

  const leaderboardQuery = useQuery({
    queryKey: ["score_leaderboard", market, leaderboardDirection, "stock"],
    queryFn: () => getScoreLeaderboard(market, leaderboardDirection, "swing", 50, "stock"),
  });

  const breadthQuery = useQuery({
    queryKey: ["advance_decline", market],
    queryFn: () => getAdvanceDecline(market),
  });

  const indexContributionQuery = useQuery({
    queryKey: ["index_contribution", market],
    queryFn: () => getIndexContribution(market),
    enabled: !isUs,
  });

  const priceMomentumQuery = useQuery({
    queryKey: ["price_momentum", market, momentumPeriod],
    queryFn: () => getPriceMomentum(market, momentumPeriod, undefined, MAX_TOP_N),
    // US no longer renders `PriceMomentumCard` (`RankingsCard`'s own tabs — a live screener
    // snapshot, not this historical %-change ranking — replaced it), so this fetch is TW-only.
    enabled: !isUs,
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <RainbowBorderCard className="flex h-full">
          <QueryState query={leaderboardQuery} skeleton={<CardSkeleton height={430} />}>
            {(data) => (
              <LeaderboardCard
                results={data.results}
                asOf={data.as_of}
                direction={leaderboardDirection}
                onDirectionChange={setLeaderboardDirection}
                market={market}
              />
            )}
          </QueryState>
        </RainbowBorderCard>

        <RainbowBorderCard className="flex h-full">
          <QueryState query={breadthQuery} skeleton={<CardSkeleton height={560} />}>
            {(breadth) =>
              isUs ? (
                <BreadthOrderBookCard breadth={breadth} indexContribution={null} market={market} />
              ) : (
                <QueryState query={indexContributionQuery} skeleton={<CardSkeleton height={560} />}>
                  {(contribution) => (
                    <BreadthOrderBookCard
                      breadth={breadth}
                      indexContribution={contribution}
                      market={market}
                    />
                  )}
                </QueryState>
              )
            }
          </QueryState>
        </RainbowBorderCard>

        <RainbowBorderCard className="flex h-full">
          {isUs ? (
            <RankingsCard market={market} />
          ) : (
            <QueryState query={priceMomentumQuery} skeleton={<CardSkeleton height={430} />}>
              {(data) => (
                <PriceMomentumCard
                  gainers={data.top_gainers}
                  losers={data.top_losers}
                  direction={momentumDirection}
                  onDirectionChange={setMomentumDirection}
                  period={momentumPeriod}
                  onPeriodChange={setMomentumPeriod}
                  market={market}
                />
              )}
            </QueryState>
          )}
        </RainbowBorderCard>

        {isUs ? (
          // Fills the grid cell 重大訊息 occupies for TW (MOPS-only, no US equivalent) with a
          // real US-backed alternative instead of leaving it empty.
          <RainbowBorderCard>
            <EarningsCalendarPanel />
          </RainbowBorderCard>
        ) : (
          <RainbowBorderCard>
            <MaterialAnnouncementFeedPanel market={market} />
          </RainbowBorderCard>
        )}
      </div>
    </div>
  );
}
