"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { UsMarketRankingRows } from "@/components/market/us-market-ranking-rows";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getUsMarketRankings } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import { PREVIEW_ROWS } from "@/lib/density";
import type { RankingKind } from "@/types/market";
import type { Market } from "@/types/settings";

// Same collapsed-row-count + "fetch the full backend cap, show 10, expand the rest in a modal"
// convention `PriceMomentumBody`/`LeaderboardCard` use — one fetch serves both the inline preview
// and the modal, so opening "展開完整" never re-fetches.
const COLLAPSED_ROW_COUNT = PREVIEW_ROWS;
const FETCH_TOP_N = 50;
const RANKING_TABS = [
  "trending",
  "market_cap",
  "revenue",
  "roe",
] as const satisfies readonly RankingKind[];

/** 排行榜 — the US 每日焦點 sub-tab's multi-tab ranking card (熱門美股/市值/營收/ROE, each a
 * live `get_us_market_rankings` screener query). Every tab shares one column set — 名稱/價格/
 * 漲跌%/走勢/成交量 (`UsMarketRankingRows`), 漲跌% click-to-sort — rather than each tab carrying
 * its own bespoke controls; there is no 日/週/月 or 漲幅/跌幅 toggle here, since a live screener
 * snapshot has no lookback period and click-to-sort on 漲跌% already covers what the old
 * gainers/losers toggle did. `漲跌幅` (a *historical* day/week/month %-change ranking,
 * `get_price_momentum`) is a fundamentally different query shape — TW's own
 * `daily-focus-panel.tsx` still renders that via `PriceMomentumCard` directly, unrelated to this
 * card. Only the active tab's own panel is ever mounted, so switching tabs never issues 4
 * upfront Yahoo-screener calls for tabs the user never opens. */
export function RankingsCard({ market }: { market: Market }): ReactNode {
  const t = useTranslations("market.cards.rankings");
  const [tab, setTab] = useState<RankingKind>("trending");

  return (
    <section className="surface-card flex w-full flex-col p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>

      <div
        role="tablist"
        aria-label={t("title")}
        className="mb-3 flex w-fit flex-wrap gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]"
      >
        {RANKING_TABS.map((kind) => (
          <RankingTabButton key={kind} active={tab === kind} onClick={() => setTab(kind)}>
            {t(_TAB_LABEL_KEY[kind])}
          </RankingTabButton>
        ))}
      </div>

      <RankingTabPanel kind={tab} market={market} />
    </section>
  );
}

const _TAB_LABEL_KEY: Record<RankingKind, string> = {
  trending: "tabTrending",
  market_cap: "tabMarketCap",
  revenue: "tabRevenue",
  roe: "tabRoe",
  volume: "tabVolume", // unused — `volume` is no longer a standalone tab, kept for schema completeness
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
          ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {children}
    </button>
  );
}

/** One tab's own fetch + render — mounted only while its tab is active (see the parent's own
 * comment), so `useQuery` firing on mount is exactly the lazy-per-tab-open fetch the module
 * docstring describes, with no `enabled` flag needed. Fetches the full `FETCH_TOP_N` cap once and
 * shows `COLLAPSED_ROW_COUNT` inline, same "展開完整" -> `ExpandModal` convention every other
 * ranked list in this dashboard (`PriceMomentumBody`/`LeaderboardCard`) uses — the same fetched
 * rows back both the inline preview and the modal, so expanding never re-fetches. */
function RankingTabPanel({
  kind,
  market,
}: {
  readonly kind: RankingKind;
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
