"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { PriceMomentumBody, PriceMomentumControls } from "@/components/market/price-momentum-card";
import { UsMarketRankingRows } from "@/components/market/us-market-ranking-rows";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getUsMarketRankings } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import type { LeaderboardDirection, MomentumPeriod, PriceMomentumRow } from "@/types/market";
import type { Market } from "@/types/settings";

// Same collapsed-row-count + "fetch the full backend cap, show 10, expand the rest in a modal"
// convention `PriceMomentumBody`/`LeaderboardCard` use — one fetch serves both the inline preview
// and the modal, so opening "展開完整" never re-fetches.
const COLLAPSED_ROW_COUNT = 10;
const FETCH_TOP_N = 50;
const NEW_RANKING_TABS = ["trending", "market_cap", "revenue", "roe", "volume"] as const;
type NewRankingTab = (typeof NEW_RANKING_TABS)[number];
type RankingsTab = "price_momentum" | NewRankingTab;

/** 排行榜 — the US 每日焦點 sub-tab's multi-tab ranking card, replacing the single-purpose
 * 漲跌幅排行 card there (`PriceMomentumCard` itself is unchanged and still used as-is for TW,
 * which has no `get_us_market_rankings` equivalent — see `daily-focus-panel.tsx`'s own `isUs`
 * branch). 漲跌幅 reuses `PriceMomentumBody`/`PriceMomentumControls` (extracted from
 * `price-momentum-card.tsx` for this purpose) against the already-fetched `get_price_momentum`
 * data; the other 5 tabs each fetch `get_us_market_rankings` lazily — only the active tab's own
 * `NewRankingTabPanel` is ever mounted, so switching tabs never issues 5 upfront Yahoo-screener
 * calls for tabs the user never opens. */
export function RankingsCard({
  gainers,
  losers,
  direction,
  onDirectionChange,
  period,
  onPeriodChange,
  market,
}: {
  gainers: readonly PriceMomentumRow[];
  losers: readonly PriceMomentumRow[];
  direction: LeaderboardDirection;
  onDirectionChange: (direction: LeaderboardDirection) => void;
  period: MomentumPeriod;
  onPeriodChange: (period: MomentumPeriod) => void;
  market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.rankings");
  const [tab, setTab] = useState<RankingsTab>("price_momentum");

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        {tab === "price_momentum" ? (
          <PriceMomentumControls
            direction={direction}
            onDirectionChange={onDirectionChange}
            period={period}
            onPeriodChange={onPeriodChange}
          />
        ) : null}
      </div>

      <div
        role="tablist"
        aria-label={t("title")}
        className="mb-3 flex w-fit flex-wrap gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]"
      >
        <RankingTabButton
          active={tab === "price_momentum"}
          onClick={() => setTab("price_momentum")}
        >
          {t("tabPriceMomentum")}
        </RankingTabButton>
        {NEW_RANKING_TABS.map((kind) => (
          <RankingTabButton key={kind} active={tab === kind} onClick={() => setTab(kind)}>
            {t(_TAB_LABEL_KEY[kind])}
          </RankingTabButton>
        ))}
      </div>

      {tab === "price_momentum" ? (
        <PriceMomentumBody
          gainers={gainers}
          losers={losers}
          direction={direction}
          market={market}
        />
      ) : (
        <NewRankingTabPanel kind={tab} market={market} />
      )}
    </section>
  );
}

const _TAB_LABEL_KEY: Record<NewRankingTab, string> = {
  trending: "tabTrending",
  market_cap: "tabMarketCap",
  revenue: "tabRevenue",
  roe: "tabRoe",
  volume: "tabVolume",
};

function RankingTabButton({
  active,
  onClick,
  children,
}: {
  readonly active: boolean;
  readonly onClick: () => void;
  readonly children: ReactNode;
}): ReactNode {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={cn(
        "rounded-[var(--radius-pill)] px-2.5 py-1 text-[12px] transition-colors",
        active
          ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {children}
    </button>
  );
}

/** One new tab's own fetch + render — mounted only while its tab is active (see the parent's
 * own comment), so `useQuery` firing on mount is exactly the lazy-per-tab-open fetch the module
 * docstring describes, with no `enabled` flag needed. Fetches the full `FETCH_TOP_N` cap once and
 * shows `COLLAPSED_ROW_COUNT` inline, same "展開完整" -> `ExpandModal` convention every other
 * ranked list in this dashboard (`PriceMomentumBody`/`LeaderboardCard`) uses — the same fetched
 * rows back both the inline preview and the modal, so expanding never re-fetches. */
function NewRankingTabPanel({
  kind,
  market,
}: {
  readonly kind: NewRankingTab;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.rankings");
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: ["us_market_rankings", kind, FETCH_TOP_N],
    queryFn: () => getUsMarketRankings(kind, FETCH_TOP_N),
  });

  if (query.isLoading) {
    return <CardSkeletonBody height={360} />;
  }
  if (query.isError || !query.data || query.data.rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>;
  }

  const rows = query.data.rows;
  const previewRows = rows.slice(0, COLLAPSED_ROW_COUNT);

  return (
    <>
      <UsMarketRankingRows rows={previewRows} market={market} />
      {rows.length > COLLAPSED_ROW_COUNT ? (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className={cn(
            "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
            "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
          )}
        >
          <span>{t("expandTo", { count: rows.length })}</span>
        </button>
      ) : null}
      <ExpandModal
        open={expanded}
        title={t(_TAB_LABEL_KEY[kind])}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <UsMarketRankingRows rows={rows} market={market} />
      </ExpandModal>
    </>
  );
}
