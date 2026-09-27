"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { HolderAccumulationRows } from "@/components/market/holder-accumulation-card";
import { SponsorTeaser } from "@/components/market/sponsor-teaser";
import { TwMarketRankingRows } from "@/components/market/tw-market-ranking-rows";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getHolderAccumulation, getTwMarketRankings } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import { isMaskedTeaser, type TwRankingKind } from "@/types/market";

// Same "fetch the full backend cap once, show 10 inline, expand the rest in a modal" convention
// `RankingsCard` (the US sibling) uses.
const COLLAPSED_ROW_COUNT = 10;
const FETCH_TOP_N = 50;
// 大戶買賣's own existing threshold/window default (`ChipDataPanel`'s own initial state) — this
// compact tab shows one fixed slice rather than carrying over `HolderAccumulationCard`'s full
// 4+4 pill controls, which would crowd a tab this narrow.
const HOLDER_THRESHOLD = 400;
const HOLDER_WINDOW = 2;

const TW_RANKING_TABS = [
  "dealer_streak",
  "trust_streak",
  "foreign_streak",
  "margin_change",
  "dividend_yield",
  "revenue_growth",
  "holder_accumulation",
] as const;
type TwRankingsTab = (typeof TW_RANKING_TABS)[number];

const _TAB_LABEL_KEY: Record<TwRankingsTab, string> = {
  dealer_streak: "tabDealerStreak",
  trust_streak: "tabTrustStreak",
  foreign_streak: "tabForeignStreak",
  margin_change: "tabMarginChange",
  dividend_yield: "tabDividendYield",
  revenue_growth: "tabRevenueGrowth",
  holder_accumulation: "tabHolderAccumulation",
};

const _EXTRA_COLUMN_LABEL_KEY: Record<TwRankingKind, string> = {
  dealer_streak: "columnStreak",
  trust_streak: "columnStreak",
  foreign_streak: "columnStreak",
  margin_change: "columnMarginChange",
  dividend_yield: "columnDividendYield",
  revenue_growth: "columnRevenueGrowth",
};

/** 排行榜 — the TW 每日焦點 sub-tab's multi-tab ranking card, replacing the old 漲跌幅排行 (moved
 * out to `PriceMomentumCard`, no longer wired here — see `daily-focus-panel.tsx`'s own comment).
 * 7 tabs: 主力/投信/外資連買賣 (`get_tw_market_rankings`'s streak kinds), 融資增減/股息殖利率/
 * 月營收成長 (its other 3 kinds), and 大戶買賣 (reuses `get_holder_accumulation` verbatim — the
 * only Sponsor-gated tab; the tab button itself is always visible, `SponsorTeaser` masks only
 * that one tab's own content on click for a free-tier session, mirroring `ChipDataPanel`'s own
 * `isMaskedTeaser` branch for the SAME tool elsewhere on this page). Only the active tab's own
 * panel is ever mounted, so switching tabs never issues 7 upfront fetches. */
export function TwRankingsCard(): ReactNode {
  const t = useTranslations("market.cards.twRankings");
  const [tab, setTab] = useState<TwRankingsTab>("dealer_streak");

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>

      <div
        role="tablist"
        aria-label={t("title")}
        className="mb-3 flex w-fit flex-wrap gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]"
      >
        {TW_RANKING_TABS.map((kind) => (
          <TwRankingTabButton key={kind} active={tab === kind} onClick={() => setTab(kind)}>
            {t(_TAB_LABEL_KEY[kind])}
          </TwRankingTabButton>
        ))}
      </div>

      {tab === "holder_accumulation" ? (
        <HolderAccumulationTabPanel />
      ) : (
        <TwRankingTabPanel kind={tab} />
      )}
    </section>
  );
}

function TwRankingTabButton({
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

/** One of the 6 `get_tw_market_rankings` tabs' own fetch + render — mounted only while its tab
 * is active, mirroring `RankingsCard`'s own `RankingTabPanel`. */
function TwRankingTabPanel({ kind }: { readonly kind: TwRankingKind }): ReactNode {
  const t = useTranslations("market.cards.twRankings");
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: ["tw_market_rankings", kind, FETCH_TOP_N],
    queryFn: () => getTwMarketRankings(kind, FETCH_TOP_N),
  });

  if (query.isLoading) {
    return <CardSkeletonBody height={360} />;
  }
  if (query.isError || !query.data || !query.data.data_available || query.data.rows.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>;
  }

  const rows = query.data.rows;
  const previewRows = rows.slice(0, COLLAPSED_ROW_COUNT);
  const extraColumnLabel = t(_EXTRA_COLUMN_LABEL_KEY[kind]);

  return (
    <>
      <TwMarketRankingRows rows={previewRows} market="TW" extraColumnLabel={extraColumnLabel} />
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
        <TwMarketRankingRows rows={rows} market="TW" extraColumnLabel={extraColumnLabel} />
      </ExpandModal>
    </>
  );
}

/** 大戶買賣 tab — reuses `get_holder_accumulation` verbatim (fixed 400張/2週 slice, no pill
 * controls in this compact tab view) via the SAME `isMaskedTeaser` branch `ChipDataPanel` already
 * uses elsewhere on this page for the identical tool; `HolderAccumulationCard`'s own bespoke row
 * rendering (not `SymbolQuoteRows`-based — see that component's own docstring for why) is reused
 * as-is rather than duplicated. Same collapse-to-10/`ExpandModal` convention as the other 6 tabs
 * (`TwRankingTabPanel`), so 大戶買賣 isn't the one tab in this card that dumps all 50 rows flat. */
function HolderAccumulationTabPanel(): ReactNode {
  const t = useTranslations("market.cards.twRankings");
  const tHolder = useTranslations("market.cards.holderAccumulation");
  const tLeaderboard = useTranslations("market.cards.leaderboard");
  const [expanded, setExpanded] = useState(false);
  const query = useQuery({
    queryKey: ["holder_accumulation_tw_rankings_tab", HOLDER_THRESHOLD, HOLDER_WINDOW],
    queryFn: () =>
      getHolderAccumulation("TW", HOLDER_THRESHOLD, HOLDER_WINDOW, undefined, FETCH_TOP_N),
  });

  if (query.isLoading) {
    return <CardSkeletonBody height={360} />;
  }
  if (query.isError || !query.data) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>;
  }
  if (isMaskedTeaser(query.data)) {
    return (
      <SponsorTeaser
        headline={tHolder("title")}
        preview={query.data.preview}
        requiredTier={query.data.required_tier}
      >
        <div className="h-52 rounded-[var(--radius-input)] bg-[var(--surface-2)]" />
      </SponsorTeaser>
    );
  }

  const rows = query.data.rows;
  const previewRows = rows.slice(0, COLLAPSED_ROW_COUNT);

  return (
    <>
      <HolderAccumulationRows rows={previewRows} t={tHolder} tLeaderboard={tLeaderboard} />
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
        title={t(_TAB_LABEL_KEY.holder_accumulation)}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <HolderAccumulationRows rows={rows} t={tHolder} tLeaderboard={tLeaderboard} />
      </ExpandModal>
    </>
  );
}
