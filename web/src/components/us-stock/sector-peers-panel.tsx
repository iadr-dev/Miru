"use client";

import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SymbolLink } from "@/components/market/symbol-link";
import { ExpandModal } from "@/components/ui/expand-modal";
import { QueryFailure } from "@/components/ui/query-state";
import { SectorIndustriesTreemap } from "@/components/us-stock/sector-industries-treemap";
import { getStockPeers } from "@/lib/api/stock-detail";
import { formatCompactNumber, formatNumber } from "@/lib/format-number";
import { directionColor, directionGlyph } from "@/lib/signed";
import type { Market } from "@/types/settings";
import type {
  IndustryOverviewCompanyRow,
  SectorIndustryWeightRow,
  SectorOverviewCompanyRow,
  SectorOverviewStats,
} from "@/types/stock/profile";

const PEER_ROW_LIMIT = 10;

/** 產業同業 tab — one symbol's sector + industry peer companies (`get_stock_peers`, which itself
 * resolves the symbol's own sector/industry classification then fetches both peer lists, the
 * sector's own aggregate stats, and its industry-weight breakdown in one call). Genuinely new:
 * no TW-detail-page precedent exists for this — TW's own 產業分析 tab (`industry-panel.tsx`) is
 * built on curated theme/supply-chain datasets that don't apply outside TW, not the same
 * underlying capability as this panel.
 *
 * Layout: a sector-industries composition treemap (real market-weight breakdown from
 * `yfinance.Sector.industries`) at the top, sector-aggregate stat cards below it, then the two
 * peer tables — the industry-peer table gains real YTD return/target price/growth-estimate
 * columns when `get_stock_peers` resolved them (only `top_growth_companies`/
 * `top_performing_companies` rows carry these; a plain `top_companies` row never does). */
export function SectorPeersPanel({
  symbol,
  market,
}: {
  readonly symbol: string;
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("usStock.sectorPeers");
  const query = useQuery({
    queryKey: ["stock_peers", symbol, market],
    queryFn: () => getStockPeers(symbol, market),
  });

  if (query.isLoading) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <CardSkeletonBody height={420} />
      </section>
    );
  }
  if (query.isError) {
    return <QueryFailure query={query} />;
  }
  const data = query.data;
  if (!data) return null;

  if (
    data.unavailable_reason &&
    data.sector_peers.length === 0 &&
    data.industry_peers.length === 0
  ) {
    return (
      <section className="surface-card p-4 sm:p-5">
        <h2 className="mb-1 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
        <p className="mt-3 text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>
      </section>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      {data.industries.length > 0 ? (
        <IndustriesTreemapSection
          title={
            data.sector
              ? t("industriesTitle", { sector: data.sector })
              : t("industriesTitleFallback")
          }
          rows={data.industries}
        />
      ) : null}

      {data.stats ? <SectorStatsCards stats={data.stats} /> : null}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <PeerCard
          title={data.sector ? t("sectorTitle", { sector: data.sector }) : t("sectorTitleFallback")}
          rows={data.sector_peers}
          symbol={symbol}
          market={market}
          emptyLabel={t("noSectorPeers")}
        />
        <PeerCard
          title={
            data.industry
              ? t("industryTitle", { industry: data.industry })
              : t("industryTitleFallback")
          }
          rows={data.industry_peers}
          symbol={symbol}
          market={market}
          emptyLabel={t("noIndustryPeers")}
        />
      </div>
    </div>
  );
}

function IndustriesTreemapSection({
  title,
  rows,
}: {
  readonly title: string;
  readonly rows: readonly SectorIndustryWeightRow[];
}): ReactNode {
  const t = useTranslations("usStock.sectorPeers");
  const locale = useLocale();
  const [expanded, setExpanded] = useState(false);

  // Descending by weight — the treemap's own squarify pass already sorts internally for layout,
  // but the list view needs its own explicit, stable rank order independent of tile geometry.
  const ranked = [...rows].sort((a, b) => b.market_weight - a.market_weight);

  return (
    <section className="surface-card p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between gap-2">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">{title}</h2>
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="shrink-0 text-[12px] text-[var(--text-muted)] transition-colors hover:text-[var(--text)]"
        >
          {t("viewAllIndustries")}
        </button>
      </div>
      <SectorIndustriesTreemap rows={rows} />
      <ExpandModal
        open={expanded}
        title={title}
        closeLabel={t("collapse")}
        onClose={() => setExpanded(false)}
      >
        <ol className="flex flex-col gap-2">
          {ranked.map((row, index) => (
            <li
              key={row.industry_key}
              className="flex items-center justify-between gap-3 border-[var(--border)] border-b py-2 last:border-b-0"
            >
              <span className="flex min-w-0 items-center gap-2.5">
                <span className="w-5 shrink-0 text-[12px] text-[var(--text-muted)] tabular-nums">
                  {index + 1}
                </span>
                <span className="truncate text-[13px] text-[var(--text)]">{row.name}</span>
              </span>
              <span className="shrink-0 font-num text-[13px] text-[var(--text)] tabular-nums">
                {formatNumber(row.market_weight * 100, locale, { decimals: 1 })}%
              </span>
            </li>
          ))}
        </ol>
      </ExpandModal>
    </section>
  );
}

function SectorStatsCards({ stats }: { readonly stats: SectorOverviewStats }): ReactNode {
  const t = useTranslations("usStock.sectorPeers");
  const locale = useLocale();

  // Same categorical `--chart-1..6` stat-row pattern `basic-info-panel.tsx::ProfileCard` already
  // uses — a magnitude/count grid, never signed, so no `--up`/`--down` here either.
  const cards: [string, string, string][] = [
    [t("statMarketCap"), formatCompactNumber(stats.market_cap ?? null, locale), "var(--chart-1)"],
    [
      t("statCompaniesCount"),
      stats.companies_count != null ? formatNumber(stats.companies_count, locale) : "—",
      "var(--chart-2)",
    ],
    [
      t("statEmployeeCount"),
      stats.employee_count != null ? formatCompactNumber(stats.employee_count, locale) : "—",
      "var(--chart-3)",
    ],
    [
      t("statMarketWeight"),
      stats.market_weight != null
        ? `${formatNumber(stats.market_weight * 100, locale, { decimals: 1 })}%`
        : "—",
      "var(--chart-4)",
    ],
  ];

  return (
    <section className="surface-card p-4 sm:p-5">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {cards.map(([label, value, color]) => (
          <div key={label}>
            <p className="text-[11px] text-[var(--text-muted)]">{label}</p>
            <p className="font-num text-[16px] tabular-nums" style={{ color }}>
              {value}
            </p>
          </div>
        ))}
      </div>
    </section>
  );
}

function PeerCard({
  title,
  rows,
  symbol,
  market,
  emptyLabel,
}: {
  readonly title: string;
  readonly rows: readonly (SectorOverviewCompanyRow | IndustryOverviewCompanyRow)[];
  readonly symbol: string;
  readonly market: Market;
  readonly emptyLabel: string;
}): ReactNode {
  const t = useTranslations("usStock.sectorPeers");
  const locale = useLocale();
  const companyRows = rows.filter((row) => row.row_kind === "company").slice(0, PEER_ROW_LIMIT);
  // `growth_company`/`performing_company` rows (industry peers only — `SectorOverviewCompanyRow`
  // never carries these) are a second, richer list: real YTD return/target price/growth-estimate
  // data from `top_growth_companies`/`top_performing_companies`, worth showing even though they
  // overlap with `top_companies` by symbol sometimes — each list answers a different question
  // ("who's in this industry" vs. "who's performing/growing best in it").
  const enrichedRows = rows.filter(
    (row): row is IndustryOverviewCompanyRow =>
      (row.row_kind === "growth_company" || row.row_kind === "performing_company") &&
      "ytd_return" in row,
  );

  return (
    <section className="surface-card p-4 sm:p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{title}</h2>
      {companyRows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{emptyLabel}</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {companyRows.map((row) => (
            <li
              key={row.symbol}
              className="flex items-center justify-between gap-3 border-[var(--border)] border-b pb-2 last:border-b-0 last:pb-0"
            >
              <SymbolLink
                symbol={row.symbol}
                market={market}
                className="min-w-0 truncate text-[13px] text-[var(--text)] hover:underline"
              >
                {row.name || row.symbol}
              </SymbolLink>
              {row.symbol === symbol ? (
                <span className="shrink-0 rounded-[var(--radius-pill)] bg-[var(--accent)]/15 px-2 py-0.5 text-[11px] text-[var(--accent)]">
                  {t("self")}
                </span>
              ) : row.rating ? (
                <span className="shrink-0 text-[11px] text-[var(--text-muted)]">{row.rating}</span>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {enrichedRows.length > 0 ? (
        <div className="mt-4 border-[var(--border)] border-t pt-3">
          <h3 className="mb-2 text-[11px] text-[var(--text-muted)] uppercase tracking-wide">
            {t("topMoversTitle")}
          </h3>
          <ul className="flex flex-col gap-2">
            {enrichedRows.slice(0, PEER_ROW_LIMIT).map((row) => (
              <li
                key={`${row.row_kind}-${row.symbol}`}
                className="flex items-center justify-between gap-2 text-[12.5px]"
              >
                <SymbolLink
                  symbol={row.symbol}
                  market={market}
                  className="min-w-0 truncate text-[var(--text)] hover:underline"
                >
                  {row.name || row.symbol}
                </SymbolLink>
                <span className="flex shrink-0 items-center gap-2 font-num text-[11.5px] text-[var(--text-muted)] tabular-nums">
                  {row.ytd_return != null ? (
                    <span
                      role="group"
                      style={{ color: directionColor(row.ytd_return) }}
                      aria-label={t("ytdReturnAria", {
                        value: formatNumber(row.ytd_return * 100, locale, { decimals: 1 }),
                      })}
                    >
                      {directionGlyph(row.ytd_return)}
                      {formatNumber(Math.abs(row.ytd_return) * 100, locale, { decimals: 1 })}%
                    </span>
                  ) : null}
                  {row.target_price != null
                    ? t("targetPrice", {
                        value: formatNumber(row.target_price, locale, { decimals: 2 }),
                      })
                    : null}
                  {row.growth_estimate != null
                    ? t("growthEstimate", {
                        value: formatNumber(row.growth_estimate * 100, locale, { decimals: 0 }),
                      })
                    : null}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
