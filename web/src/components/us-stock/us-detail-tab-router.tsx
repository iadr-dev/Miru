"use client";

import type { ReactNode } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { UsEtfTabBar } from "@/components/us-stock/us-etf-tab-bar";
import { UsStockTabBar } from "@/components/us-stock/us-stock-tab-bar";
import { useIsUsFund } from "@/hooks/use-is-us-fund";
import type { Market } from "@/types/settings";

/** Picks `UsStockTabBar` vs `UsEtfTabBar` for a US symbol — client-side, since the only real
 * fund-vs-stock signal (`get_fund_holdings` resolving non-empty) requires a fetch, and this
 * codebase's whole auth/data-fetch flow is browser-cookie-based (no RSC-server-fetch precedent
 * to cheaply pre-resolve this on the server; see the decision this component implements).
 * Renders a neutral tab-shaped skeleton while that one fetch is in flight — for the common case
 * (a plain US stock) this resolves fast and is barely visible; for a real US ETF it means one
 * loading frame instead of a stock tab bar flashing in and then being replaced. */
export function UsDetailTabRouter({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const { isFund, isLoading } = useIsUsFund(symbol, market);

  if (isLoading) {
    return (
      <div className="flex flex-col gap-4 p-5">
        <div className="h-11 w-full border-[var(--border)] border-b" aria-hidden />
        <CardSkeletonBody height={360} />
      </div>
    );
  }
  return isFund ? (
    <UsEtfTabBar symbol={symbol} market={market} />
  ) : (
    <UsStockTabBar symbol={symbol} market={market} />
  );
}
