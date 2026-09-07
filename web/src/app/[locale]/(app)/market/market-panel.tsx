"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { EtfTabContent } from "@/components/market/etf-tab-content";
import { MarketTabPanels } from "@/components/market/market-tabs";
import { TwTabContent } from "@/components/market/tw-tab-content";
import { useDataUpdates } from "@/hooks/use-data-updates";
import { MarketApiError } from "@/lib/api/market";
import type { Market } from "@/types/settings";

const DEFAULT_MARKET: Market = "TW";

export function MarketPanel(): ReactNode {
  useDataUpdates();

  return (
    <div className="p-6">
      <MarketTabPanels
        renderTw={() => <TwTabContent market={DEFAULT_MARKET} />}
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
