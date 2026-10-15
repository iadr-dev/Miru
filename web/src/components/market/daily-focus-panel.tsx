"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { BreadthOrderBookCard } from "@/components/market/breadth-order-book-card";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { EarningsCalendarPanel } from "@/components/market/earnings-calendar-panel";
import { LeaderboardCard } from "@/components/market/leaderboard-card";
import { MaterialAnnouncementFeedPanel } from "@/components/market/material-announcement-feed-panel";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { RankingsCard } from "@/components/market/rankings-card";
import { TwRankingsCard } from "@/components/market/tw-rankings-card";
import { QueryState } from "@/components/ui/query-state";
import { getAdvanceDecline, getIndexContribution, getScoreLeaderboard } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import type { LeaderboardDirection } from "@/types/market/rankings";
import type { Market } from "@/types/settings";

/**
 * 每日焦點 — the market tab's default sub-tab: AI 多空排行 (whole-market score leaderboard),
 * 漲跌家數 (+ 指數貢獻排行 for TW only) breadth card, and 排行榜 (TW: `TwRankingsCard`'s 7 tabs;
 * US: `RankingsCard`'s 4 tabs — 漲跌幅排行/`PriceMomentumCard` no longer renders in either
 * market's grid cell, replaced by `TwRankingsCard` for TW the same way `RankingsCard` already
 * replaced it for US). These cards previously rendered directly in `MarketPanel`'s `renderTw`
 * with no sub-tab wrapper — pulled out here as the TW tab grows a second sub-tab (籌碼),
 * mirroring `TwTabContent`'s sibling
 * `ChipDataPanel`.
 *
 * US variant (`market === "US"`) drops two cards with no US backing data: 指數貢獻排行 (no real
 * US index is ingested — only the SPY ETF proxy exists, not a genuine index-contribution
 * dataset) and 重大訊息 (MOPS material announcements are TW-only). The grid falls back to 2
 * columns x 2 rows instead of the TW 4-card layout.
 */
export function DailyFocusPanel({ market }: { readonly market: Market }): ReactNode {
  const [leaderboardDirection, setLeaderboardDirection] = useState<LeaderboardDirection>("bullish");
  const isUs = market === "US";

  const leaderboardQuery = useQuery({
    queryKey: queryKeys.marketRead("score_leaderboard", market, leaderboardDirection, "stock"),
    queryFn: () => getScoreLeaderboard(market, leaderboardDirection, "swing", 50, "stock"),
  });

  const breadthQuery = useQuery({
    queryKey: queryKeys.marketRead("advance_decline", market),
    queryFn: () => getAdvanceDecline(market),
  });

  const indexContributionQuery = useQuery({
    queryKey: queryKeys.marketRead("index_contribution", market),
    queryFn: () => getIndexContribution(market),
    enabled: !isUs,
  });

  return (
    <div className="flex flex-col gap-4">
      {/* Grid rows stretch, so the two cards side by side always share one height (a card's own
          footer is pinned to its bottom with mt-auto). */}
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
                assetClass="stock"
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
          {isUs ? <RankingsCard market={market} /> : <TwRankingsCard />}
        </RainbowBorderCard>

        {isUs ? (
          // Fills the grid cell 重大訊息 occupies for TW (MOPS-only, no US equivalent) with a
          // real US-backed alternative instead of leaving it empty.
          <RainbowBorderCard className="flex h-full">
            <EarningsCalendarPanel />
          </RainbowBorderCard>
        ) : (
          <RainbowBorderCard className="flex h-full">
            <MaterialAnnouncementFeedPanel market={market} />
          </RainbowBorderCard>
        )}
      </div>
    </div>
  );
}
