"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { LeaderboardCard } from "@/components/market/leaderboard-card";
import { MarginMaintenanceCard } from "@/components/market/margin-maintenance-card";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import {
  getMarginMaintenance,
  getMarginShortBalance,
  getScoreLeaderboard,
  MarketApiError,
} from "@/lib/api/market";
import type { LeaderboardDirection, LeaderboardUniverse } from "@/types/market";
import { isMaskedTeaser } from "@/types/market";
import type { Market } from "@/types/settings";

/** A bounded ~1-year window ending today so `MarginMaintenanceCard`'s trend rows (今日/前一交易日/
 * 一週前/一個月前/一年前) each have a real fetched row to match against — never a client-side
 * extrapolation past what this range actually returned. The 365-day anchor for a row near the
 * end of the window needs `nearestOnOrBefore` to find a real trading day AT OR BEFORE it inside
 * the fetched range; a weekend/holiday sitting right on the anchor pushes the nearest real row
 * earlier still, so the window's start must clear the anchor by more than a couple of days.
 * Taiwan's longest closure (Lunar New Year) runs up to ~9 calendar days — 20 days of slack
 * comfortably covers that plus an ordinary weekend, without over-fetching by much. */
function oneYearWindow(): { startDate: string; endDate: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 385);
  return { startDate: start.toISOString().slice(0, 10), endDate: end.toISOString().slice(0, 10) };
}

export function TopSplit({ market }: { market: Market }): ReactNode {
  const t = useTranslations("market.cards");
  const [direction, setDirection] = useState<LeaderboardDirection>("bullish");
  const [universe, setUniverse] = useState<LeaderboardUniverse>("stock");
  const marginWindow = oneYearWindow();

  const leaderboardQuery = useQuery({
    queryKey: ["score_leaderboard", market, direction, universe],
    queryFn: () => getScoreLeaderboard(market, direction, "swing", 50, universe),
  });

  const marginQuery = useQuery({
    queryKey: ["margin_maintenance_ratio", market, marginWindow.startDate, marginWindow.endDate],
    queryFn: () => getMarginMaintenance(market, marginWindow),
  });

  const marginShortBalanceQuery = useQuery({
    queryKey: ["margin_short_total_market", market],
    queryFn: () => getMarginShortBalance(market),
  });

  return (
    <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-[1.4fr_1fr]">
      <QueryState query={leaderboardQuery}>
        {(data) => (
          <RainbowBorderCard className="flex h-full">
            <LeaderboardCard
              results={data.results}
              asOf={data.as_of}
              direction={direction}
              onDirectionChange={setDirection}
              universe={universe}
              onUniverseChange={setUniverse}
              market={market}
            />
          </RainbowBorderCard>
        )}
      </QueryState>

      <QueryState query={marginQuery}>
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
            <RainbowBorderCard className="flex h-full">
              <MarginMaintenanceCard
                rows={data.rows}
                shortBalanceRows={
                  marginShortBalanceQuery.data && !isMaskedTeaser(marginShortBalanceQuery.data)
                    ? marginShortBalanceQuery.data.rows
                    : []
                }
              />
            </RainbowBorderCard>
          );
        }}
      </QueryState>
    </div>
  );
}

/** Shared loading/empty/error branching, matching `market-panel.tsx`'s own `QueryState` — kept
 * local here since this component owns its own two queries and both need the same shape. */
function QueryState<T>({
  query,
  children,
}: {
  query: {
    isLoading: boolean;
    isError: boolean;
    error: unknown;
    data: T | undefined;
    refetch: () => void;
  };
  children: (data: T) => ReactNode;
}): ReactNode {
  const t = useTranslations("market.cards");

  if (query.isLoading) {
    return (
      <div className="animate-pulse space-y-2" aria-hidden>
        <div className="h-52 rounded-[var(--radius-card)] bg-[var(--surface-2)]" />
      </div>
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
