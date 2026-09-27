"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SymbolQuoteRows } from "@/components/market/symbol-quote-rows";
import { ExpandModal } from "@/components/ui/expand-modal";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import {
  getSupplyChainEdges,
  getSymbolThemes,
  getThemeMembers,
  getThemeTierHeatmap,
} from "@/lib/api/stock-detail";
import { cn } from "@/lib/cn";
import { heatColor } from "@/lib/heat-color";
import type { Market } from "@/types/settings";
import type { SubIndustryHeat, SupplyChainEdge } from "@/types/stock-detail";

const TIER_ORDER = ["upstream", "midstream", "downstream"] as const;
type TierName = (typeof TIER_ORDER)[number];

const EDGE_TYPE_COLOR: Record<SupplyChainEdge["edge_type"], string> = {
  supplies: "var(--up)",
  customer: "var(--chart-2)",
  competes: "var(--down)",
};

const VIEWS = ["tiers", "graph"] as const;
type IndustryView = (typeof VIEWS)[number];

/** 個股 › 產業分析's 供應鏈/產業鏈 tab — two views over two distinct real data sources:
 *
 * 1. **產業鏈** (`TierHeatmapView`) — the stock's theme's upstream/midstream/downstream
 *    sub-industry tiles (`get_theme_tiers`' own grouping), each additionally colored by that
 *    sub-industry's real average member price-change (`get_theme_tier_heatmap`), matching the
 *    reference app's own 產業鏈 tier-tile screen. A tile's tier assignment is disclosed as
 *    curated (editorially verified) or heuristic (keyword-classified) via a dotted vs solid
 *    border — a heuristic assignment is a real classification, never presented with the same
 *    confidence as a curated one.
 * 2. **供應鏈** (`SupplyChainGraphView`) — every real supply/customer/competitor edge involving
 *    this symbol (`get_supply_chain_edges`), rendered as a focus-node radial graph.
 *
 * A symbol with no theme membership yet renders an honest empty state, never a fabricated
 * industry. `docs/web/stock-detail/產業分析.md`'s original 同業比較/多空訊號 cards are NOT built
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
    queryKey: ["symbol_themes", symbol, market],
    queryFn: () => getSymbolThemes(symbol, market),
  });

  if (themesQuery.isLoading) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <CardSkeletonBody height={280} />
      </section>
    );
  }
  // Industry-chain membership only — a concept-theme (研究議題) membership carries no
  // upstream/midstream/downstream tier structure, so it can't back this tab's tier view.
  const industryTheme = themesQuery.data?.themes.find((theme) => theme.kind === "industry");

  return (
    <div className="flex flex-col gap-4">
      <div
        role="tablist"
        aria-label={t("viewSwitcherLabel")}
        className="flex w-fit gap-1 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] p-1"
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
                ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(key === "tiers" ? "tiersView" : "graphView")}
          </button>
        ))}
      </div>

      {!industryTheme ? (
        <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
          <p className="text-[13px] text-[var(--text-muted)]">{t("noIndustryTheme")}</p>
        </section>
      ) : view === "tiers" ? (
        <TierHeatmapView
          themeKey={industryTheme.theme_key}
          themeName={industryTheme.name_zh}
          market={market}
        />
      ) : (
        <SupplyChainGraphView symbol={symbol} market={market} />
      )}
    </div>
  );
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
    queryKey: ["theme_tier_heatmap", themeKey],
    queryFn: () => getThemeTierHeatmap(themeKey),
  });
  const [selectedSubIndustry, setSelectedSubIndustry] = useState<string | null>(null);

  if (query.isLoading) return <CardSkeletonBody height={320} />;
  const data = query.data;
  if (!data?.found) return null;

  if (!data.data_available) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
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
      {data.trade_date ? (
        <p className="mt-3 text-[11px] text-[var(--text-muted)]">
          {t("asOfDate", { date: data.trade_date })}
        </p>
      ) : null}

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
  const up = hasChange && (sub.pct_change ?? 0) >= 0;
  const arrow = !hasChange ? "" : up ? "▲" : (sub.pct_change ?? 0) < 0 ? "▼" : "";
  const pctLabel = hasChange
    ? `${formatNumber(sub.pct_change ?? 0, locale, { decimals: 2 })}%`
    : t("noPriceData");
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={`${sub.sub_industry} ${pctLabel} — ${t("viewMembers")}`}
      title={sub.tier_source === "heuristic" ? t("heuristicHint") : t("curatedHint")}
      className={cn(
        "flex min-w-[104px] flex-col gap-0.5 rounded-[var(--radius-input)] border-2 px-3 py-2 text-left transition-opacity hover:opacity-90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
        sub.tier_source === "heuristic" ? "border-dashed" : "border-solid",
      )}
      style={{
        backgroundColor: hasChange ? heatColor(sub.pct_change ?? 0) : "var(--surface-2)",
        borderColor: "var(--bg)",
      }}
    >
      <span className="truncate font-medium text-[12.5px] text-[color:#0e1319]">
        {sub.sub_industry}
      </span>
      <span className="font-num text-[12px] text-[color:#0e1319] tabular-nums">
        {arrow}
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
    queryKey: ["theme_members", themeKey],
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

  return (
    <ExpandModal
      open={subIndustry !== null}
      title={subIndustry ?? ""}
      closeLabel={t("closeMembers")}
      onClose={onClose}
    >
      {membersQuery.isLoading ? (
        <CardSkeletonBody height={280} />
      ) : memberSymbols.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("noMembers")}</p>
      ) : (
        <SymbolQuoteRows
          rows={memberSymbols.map((symbol) => ({ symbol }))}
          quoteBySymbol={quoteBySymbol}
          market={market}
          gridCols="grid-cols-[1.3fr_0.8fr_0.8fr_0.9fr_0.9fr]"
        />
      )}
    </ExpandModal>
  );
}

// ---------------------------------------------------------------------------
// 供應鏈 — get_supply_chain_edges (public)
// ---------------------------------------------------------------------------

interface GraphNode {
  symbol: string;
  name: string;
  edgeType: SupplyChainEdge["edge_type"];
  angle: number;
}

function SupplyChainGraphView({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("stock.industry");
  const query = useQuery({
    queryKey: ["supply_chain_edges", symbol, market],
    queryFn: () => getSupplyChainEdges(symbol, market),
  });
  const [hovered, setHovered] = useState<string | null>(null);

  const nodes = useMemo<GraphNode[]>(() => {
    const edges = query.data?.edges ?? [];
    const peers = edges.map((edge) => {
      const isFocusFrom = edge.from_symbol === symbol;
      return {
        symbol: isFocusFrom ? edge.to_symbol : edge.from_symbol,
        name: isFocusFrom ? edge.to_name : edge.from_name,
        edgeType: edge.edge_type,
      };
    });
    return peers.map((peer, index) => ({
      ...peer,
      angle: (index / Math.max(peers.length, 1)) * 2 * Math.PI,
    }));
  }, [query.data, symbol]);

  if (query.isLoading) return <CardSkeletonBody height={420} />;
  const data = query.data;
  if (!data) return null;

  if (data.edge_count === 0) {
    return (
      <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">
          {t("supplyChainTitle")}
        </h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("supplyChainUnavailable")}</p>
      </section>
    );
  }

  const radius = 42;
  const focusName =
    data.edges.find((e) => e.from_symbol === symbol)?.from_name ??
    data.edges.find((e) => e.to_symbol === symbol)?.to_name ??
    symbol;

  return (
    <section className="rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("supplyChainTitle")}</h2>
      <p className="mb-4 text-[11px] text-[var(--text-muted)]">{t("supplyChainHint")}</p>
      <p className="sr-only">
        {t("supplyChainGraphAria", { name: focusName, count: data.edge_count })}
      </p>
      <div className="relative mx-auto aspect-square w-full max-w-[420px]">
        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox="-50 -50 100 100"
          aria-hidden
          role="presentation"
        >
          {nodes.map((node) => {
            const x = radius * Math.cos(node.angle);
            const y = radius * Math.sin(node.angle);
            const isHovered = hovered === node.symbol;
            return (
              <line
                key={node.symbol}
                x1={0}
                y1={0}
                x2={x}
                y2={y}
                stroke={EDGE_TYPE_COLOR[node.edgeType]}
                strokeWidth={isHovered ? 1.4 : 0.6}
                strokeOpacity={isHovered || hovered === null ? 0.85 : 0.25}
              />
            );
          })}
        </svg>

        <div
          className="absolute top-1/2 left-1/2 flex size-16 -translate-x-1/2 -translate-y-1/2 flex-col items-center justify-center rounded-full border-2 border-[var(--accent)] bg-[var(--surface-2)] text-center"
          aria-hidden
        >
          <span className="truncate px-1 font-semibold text-[11px] text-[var(--text)]">
            {focusName}
          </span>
        </div>

        {nodes.map((node) => {
          const x = radius * Math.cos(node.angle);
          const y = radius * Math.sin(node.angle);
          return (
            <button
              key={node.symbol}
              type="button"
              onMouseEnter={() => setHovered(node.symbol)}
              onMouseLeave={() =>
                setHovered((current) => (current === node.symbol ? null : current))
              }
              onFocus={() => setHovered(node.symbol)}
              onBlur={() => setHovered((current) => (current === node.symbol ? null : current))}
              aria-label={`${node.name} — ${t(`edgeType.${node.edgeType}` as const)}`}
              className="absolute flex max-w-[92px] -translate-x-1/2 -translate-y-1/2 flex-col items-center gap-1 rounded-[var(--radius-input)] px-1.5 py-1 text-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
              style={{
                left: `${50 + x}%`,
                top: `${50 + y}%`,
                backgroundColor: hovered === node.symbol ? "var(--surface-3)" : "transparent",
              }}
            >
              <span
                aria-hidden
                className="size-2.5 rounded-full"
                style={{ backgroundColor: EDGE_TYPE_COLOR[node.edgeType] }}
              />
              <span className="truncate text-[11px] text-[var(--text)]">{node.name}</span>
            </button>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap justify-center gap-4 text-[11px] text-[var(--text-muted)]">
        {(["supplies", "customer", "competes"] as const).map((edgeType) => (
          <span key={edgeType} className="flex items-center gap-1.5">
            <span
              aria-hidden
              className="size-2 rounded-full"
              style={{ backgroundColor: EDGE_TYPE_COLOR[edgeType] }}
            />
            {t(`edgeType.${edgeType}` as const)}
          </span>
        ))}
      </div>
    </section>
  );
}
