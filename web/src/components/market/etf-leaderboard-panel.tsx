"use client";

import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { LeaderboardCard } from "@/components/market/leaderboard-card";
import { QueryState } from "@/components/ui/query-state";
import { getScoreLeaderboard } from "@/lib/api/market";
import { queryKeys } from "@/lib/api/query-keys";
import type { LeaderboardDirection } from "@/types/market/rankings";
import type { Market } from "@/types/settings";

/** AI 多空排行 scoped to the ETF universe — same `get_score_leaderboard` query `TopSplit` already
 * runs for the TW tab's stock ranking, just fixed to `universe: "etf"` here so it reads as
 * the ETF tab's own leaderboard sub-tab rather than duplicating `TopSplit`'s stock/margin layout.
 * `LeaderboardCard` no longer exposes a universe toggle itself — each caller now owns a fixed
 * universe (this one "etf", `TopSplit`'s "stock"), since the ETF tab getting its own sub-tab
 * made an in-card stock/ETF switch redundant. */
export function EtfLeaderboardPanel({ market }: { market: Market }): ReactNode {
  const [direction, setDirection] = useState<LeaderboardDirection>("bullish");

  const query = useQuery({
    queryKey: queryKeys.marketRead("score_leaderboard", market, direction, "etf"),
    queryFn: () => getScoreLeaderboard(market, direction, "swing", 50, "etf"),
  });

  return (
    <QueryState query={query} skeleton={<CardSkeleton height={430} />}>
      {(data) => (
        <LeaderboardCard
          results={data.results}
          asOf={data.as_of}
          direction={direction}
          onDirectionChange={setDirection}
          market={market}
          assetClass="etf"
        />
      )}
    </QueryState>
  );
}
