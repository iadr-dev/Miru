"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { BreadthOrderBookCard } from "@/components/market/breadth-order-book-card";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { LeaderboardCard } from "@/components/market/leaderboard-card";
import { MaterialAnnouncementFeedPanel } from "@/components/market/material-announcement-feed-panel";
import { PriceMomentumCard } from "@/components/market/price-momentum-card";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
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
 * 每日焦點 — the TW tab's default sub-tab: AI 多空排行 (whole-market score leaderboard),
 * 漲跌家數 + 指數貢獻排行 (breadth/contribution), and 漲跌幅排行 (price momentum). These three
 * cards previously rendered directly in `MarketPanel`'s `renderTw` with no sub-tab wrapper —
 * pulled out here as the TW tab grows a second sub-tab (籌碼) per `docs/web/market.md`'s own
 * Row-2 view-tab spec, mirroring `TwTabContent`'s sibling `ChipDataPanel`.
 */
export function DailyFocusPanel({ market }: { readonly market: Market }): ReactNode {
  const [momentumDirection, setMomentumDirection] = useState<LeaderboardDirection>("bullish");
  const [momentumPeriod, setMomentumPeriod] = useState<MomentumPeriod>("week");
  const [leaderboardDirection, setLeaderboardDirection] = useState<LeaderboardDirection>("bullish");

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
  });

  const priceMomentumQuery = useQuery({
    queryKey: ["price_momentum", market, momentumPeriod],
    queryFn: () => getPriceMomentum(market, momentumPeriod, undefined, MAX_TOP_N),
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

        <RainbowBorderCard>
          <QueryState query={breadthQuery} skeleton={<CardSkeleton height={560} />}>
            {(breadth) => (
              <QueryState query={indexContributionQuery} skeleton={<CardSkeleton height={560} />}>
                {(contribution) => (
                  <BreadthOrderBookCard breadth={breadth} indexContribution={contribution} />
                )}
              </QueryState>
            )}
          </QueryState>
        </RainbowBorderCard>

        <RainbowBorderCard className="flex h-full">
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
        </RainbowBorderCard>

        {/* Fills the grid cell to 漲跌幅排行's right — the grid has 3 cards on a 2-col layout,
            leaving that cell empty otherwise. */}
        <RainbowBorderCard>
          <MaterialAnnouncementFeedPanel market={market} />
        </RainbowBorderCard>
      </div>
    </div>
  );
}
