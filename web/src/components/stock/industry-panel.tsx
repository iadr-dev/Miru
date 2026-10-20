"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SymbolQuoteRows } from "@/components/market/symbol-quote-rows";
import { ChainPositionBar } from "@/components/stock/chain-position";
import { ConceptThemeChips } from "@/components/stock/concept-theme-chips";
import { EntityGraph } from "@/components/stock/entity-graph";
import { SupplyChainGraph } from "@/components/stock/supply-chain-graph";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryFailure } from "@/components/ui/query-state";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { queryKeys } from "@/lib/api/query-keys";
import { getSymbolThemes, getThemeTierHeatmap } from "@/lib/api/stock-detail";
import { getThemeMembers } from "@/lib/api/theme";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import { heatTint } from "@/lib/heat-color";
import { directionGlyph } from "@/lib/signed";
import type { Market } from "@/types/settings";
import type { SubIndustryHeat } from "@/types/stock/profile";

const TIER_ORDER = ["upstream", "midstream", "downstream"] as const;
type TierName = (typeof TIER_ORDER)[number];

const VIEWS = ["tiers", "graph", "entities"] as const;
type IndustryView = (typeof VIEWS)[number];

const VIEW_LABEL_KEY = {
  tiers: "tiersView",
  graph: "graphView",
  entities: "entitiesView",
} as const satisfies Record<IndustryView, string>;

/** 個股 › 產業分析's 供應鏈/產業鏈 tab — two views over two distinct real data sources:
 *
 * 1. **產業鏈** (`TierHeatmapView`) — the stock's theme's upstream/midstream/downstream
 *    sub-industry tiles (`get_theme_tiers`' own grouping), each additionally colored by that
 *    sub-industry's real average member price-change (`get_theme_tier_heatmap`), matching the
 *    reference app's own 產業鏈 tier-tile screen. A tile's tier assignment is disclosed as
 *    curated (editorially verified) or heuristic (keyword-classified) via a dotted vs solid
 *    border — a heuristic assignment is a real classification, never presented with the same
 *    confidence as a curated one.
 * 2. **供應鏈** (`SupplyChainGraph`) — every real supply/customer/competitor edge involving
 *    this symbol (`get_supply_chain_edges`), rendered as a focus-node radial graph.
 * 3. **關聯圖譜** (`EntityGraph`) — the technologies/materials/applications the company is
 *    mentioned with and the other companies that share them; mentions, never a claimed
 *    business relationship unless a link is a verified supply edge.
 *
 * Above the views, 所屬題材 chips link to the concept-theme pages the stock belongs to.
 *
 * A symbol with no theme membership yet renders an honest empty state, never a fabricated
 * industry. The original 同業比較/多空訊號 cards are NOT built
 * here — 同業比較 has no reliable backing (no industry-filterable peer tool short of a costly
 * per-peer live fan-out) and 多空訊號 doesn't belong on this tab (it's an MA-position read, not
 * an industry-relationship view); 產業鏈資金流 already ships on 大盤's own 籌碼 tab
 * (`chip-data-panel.tsx`) as a whole-market aggregate, not repeated here. */
export function IndustryPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const [view, setView] = useState<IndustryView>("tiers");
  const themesQuery = useQuery({
    queryKey: queryKeys.symbolRead("symbol_themes", symbol, market),
    queryFn: () => getSymbolThemes(symbol, market),
  });

  if (themesQuery.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <CardSkeletonBody height={280} />
      </section>
    );
  }
  if (themesQuery.isError) {
    return <QueryFailure query={themesQuery} />;
  }
  // Industry-chain membership only — a concept-theme (研究議題) membership carries no
  // upstream/midstream/downstream tier structure, so it can't back this tab's tier view.
  const industryTheme = themesQuery.data?.themes.find((theme) => theme.kind === "industry");

  return (
    <div className="flex flex-col gap-4">
      <div
        role="tablist"
        aria-label={t("viewSwitcherLabel")}
        className="flex w-fit max-w-full gap-1 overflow-x-auto rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] p-1"
      >
        {VIEWS.map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={view === key}
            onClick={() => setView(key)}
            className={cn(
              "min-h-9 rounded-[var(--radius-pill)] px-3.5 text-[13px] transition-colors",
              view === key
                ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(VIEW_LABEL_KEY[key])}
          </button>
        ))}
      </div>

      <ConceptThemeChips
        themes={themesQuery.data?.themes ?? []}
        locked={themesQuery.data?.concept_locked ?? false}
      />

      <ChainPositionBar symbol={symbol} market={market} />

      <IndustryViewBody view={view} symbol={symbol} market={market} industryTheme={industryTheme} />
    </div>
  );
}

/** The selected sub-view. 關聯圖譜 reads the entity graph and so does not need an industry-chain
 * classification; the other two do. */
function IndustryViewBody({
  view,
  symbol,
  market,
  industryTheme,
}: {
  readonly view: IndustryView;
  readonly symbol: string;
  readonly market: Market;
  readonly industryTheme: { readonly theme_key: string; readonly name_zh: string } | undefined;
}): ReactNode {
  const t = useTranslations("stock.industry");
  if (view === "entities") {
    return <EntityGraph symbol={symbol} market={market} />;
  }
  if (!industryTheme) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <p className="text-[13px] text-[var(--text-muted)]">{t("noIndustryTheme")}</p>
      </section>
    );
  }
  if (view === "tiers") {
    return (
      <TierHeatmapView
        themeKey={industryTheme.theme_key}
        themeName={industryTheme.name_zh}
        market={market}
      />
    );
  }
  return <SupplyChainGraph symbol={symbol} market={market} />;
}

// ---------------------------------------------------------------------------
// 產業鏈 — get_theme_tier_heatmap (public)
// ---------------------------------------------------------------------------

function TierHeatmapView({
  themeKey,
  themeName,
  market,
}: {
  readonly themeKey: string;
  readonly themeName: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const query = useQuery({
    queryKey: queryKeys.read("theme_tier_heatmap", themeKey),
    queryFn: () => getThemeTierHeatmap(themeKey),
  });
  const [selectedSubIndustry, setSelectedSubIndustry] = useState<string | null>(null);

  if (query.isLoading) return <CardSkeletonBody height={320} />;
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const data = query.data;
  if (!data?.found) return null;

  if (!data.data_available) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">
          {t("tierChainTitle", { name: themeName })}
        </h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">
          {data.unavailable_reason ?? t("tierUnavailable")}
        </p>
      </section>
    );
  }

  const byTier: Record<TierName, readonly SubIndustryHeat[]> = {
    upstream: data.upstream,
    midstream: data.midstream,
    downstream: data.downstream,
  };

  return (
    <section className="surface-card p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">
        {t("tierChainTitle", { name: themeName })}
      </h2>
      <div className="flex flex-col gap-4">
        {TIER_ORDER.map((tier) =>
          byTier[tier].length === 0 ? null : (
            <div key={tier}>
              <p className="mb-2 text-[12px] text-[var(--text-muted)]">
                {t(`tier.${tier}` as const)}
              </p>
              <div className="flex flex-wrap gap-2">
                {byTier[tier].map((sub) => (
                  <SubIndustryTile
                    key={sub.sub_industry}
                    sub={sub}
                    onClick={() => setSelectedSubIndustry(sub.sub_industry)}
                  />
                ))}
              </div>
            </div>
          ),
        )}
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[var(--text-muted)]">
        {data.trade_date ? <span>{t("asOfDate", { date: data.trade_date })}</span> : null}
        {[...data.upstream, ...data.midstream, ...data.downstream].some(
          (sub) => sub.tier_source === "heuristic",
        ) ? (
          <span className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="inline-block h-3 w-5 rounded-[3px] border border-[var(--border-strong)] border-dashed"
            />
            {t("heuristicHint")}
          </span>
        ) : null}
      </div>

      <SubIndustryMembersModal
        themeKey={themeKey}
        subIndustry={selectedSubIndustry}
        market={market}
        onClose={() => setSelectedSubIndustry(null)}
      />
    </section>
  );
}

function SubIndustryTile({
  sub,
  onClick,
}: {
  readonly sub: SubIndustryHeat;
  readonly onClick: () => void;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const locale = useLocale();
  const hasChange = sub.pct_change !== null && sub.pct_change !== undefined;
  const arrow = hasChange ? directionGlyph(sub.pct_change ?? 0, 2) : "";
  const pctLabel = hasChange
    ? `${formatNumber(sub.pct_change ?? 0, locale, { decimals: 2 })}%`
    : t("noPriceData");
  const tint = hasChange ? heatTint(sub.pct_change ?? 0) : null;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`${sub.sub_industry} ${pctLabel} — ${t("viewMembers")}`}
      title={sub.tier_source === "heuristic" ? t("heuristicHint") : t("curatedHint")}
      className={cn(
        "flex min-w-[112px] flex-col gap-0.5 rounded-[var(--radius-input)] border px-3 py-2 text-left",
        "transition-[filter,border-color] duration-[var(--dur-base)] ease-[var(--ease-out)]",
        "hover:brightness-110 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
        sub.tier_source === "heuristic"
          ? "border-[var(--border-strong)] border-dashed"
          : "border-[var(--border)] border-solid",
      )}
      style={{
        backgroundColor: tint ? tint.background : "var(--surface-2)",
        boxShadow: tint ? `inset 3px 0 0 ${tint.edge}` : undefined,
      }}
    >
      <span
        className={cn(
          "truncate font-medium text-[12.5px]",
          hasChange ? "text-[var(--text)]" : "text-[var(--text-muted)]",
        )}
      >
        {sub.sub_industry}
      </span>
      <span
        className={cn(
          "font-num text-[12px] tabular-nums",
          hasChange ? "text-[var(--text)]" : "text-[var(--text-muted)]",
        )}
      >
        {arrow ? (
          <span aria-hidden style={{ color: tint?.edge }}>
            {arrow}
          </span>
        ) : null}
        {pctLabel}
      </span>
    </button>
  );
}

/** Opens on a tile click — every member symbol of the clicked sub-industry, in the same
 * 名稱+代號/價格/漲跌%/走勢 shape every other symbol list in this app uses (`SymbolQuoteRows`).
 * `get_theme_members` has no server-side sub_industry filter, so the full theme membership is
 * fetched once (per theme, cached by `themeKey` alone) and filtered client-side per click —
 * cheap, since a theme's member list (hundreds of rows, symbol+sub_industry only) is far smaller
 * than fanning out a live quote per click. */
function SubIndustryMembersModal({
  themeKey,
  subIndustry,
  market,
  onClose,
}: {
  readonly themeKey: string;
  readonly subIndustry: string | null;
  readonly market: Market;
  readonly onClose: () => void;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const membersQuery = useQuery({
    queryKey: queryKeys.themeMembers(themeKey),
    queryFn: () => getThemeMembers(themeKey),
    enabled: subIndustry !== null,
  });
  const memberSymbols = useMemo(
    () =>
      subIndustry === null
        ? []
        : (membersQuery.data?.members ?? [])
            .filter((m) => m.sub_industry === subIndustry)
            .map((m) => m.symbol),
    [membersQuery.data, subIndustry],
  );
  const { quotes } = useSymbolQuotes(memberSymbols, market);
  const quoteBySymbol = useMemo(() => new Map(quotes.map((q) => [q.code, q])), [quotes]);

  let body: ReactNode;
  if (membersQuery.isLoading) {
    body = <CardSkeletonBody height={280} />;
  } else if (memberSymbols.length === 0) {
    body = <p className="text-[13px] text-[var(--text-muted)]">{t("noMembers")}</p>;
  } else {
    body = (
      <SymbolQuoteRows
        rows={memberSymbols.map((symbol) => ({ symbol }))}
        quoteBySymbol={quoteBySymbol}
        market={market}
        assetClass="stock"
        gridCols="grid-cols-[1.3fr_0.8fr_0.8fr_0.9fr_0.9fr_28px]"
      />
    );
  }

  return (
    <ExpandModal
      open={subIndustry !== null}
      title={subIndustry ?? ""}
      closeLabel={t("closeMembers")}
      onClose={onClose}
    >
      {body}
    </ExpandModal>
  );
}
