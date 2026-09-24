"use client";

import { SparkleIcon, StarIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { SectorChip } from "@/components/stock/sector-chip";
import { getEtfFundInfo, getEtfThemeTags } from "@/lib/api/etf-detail";
import { getEtfList, getPriceHistory, getSymbolInfo } from "@/lib/api/market";
import { getStockScore } from "@/lib/api/stock-detail";
import { hashToChartToken } from "@/lib/chart-token-hash";
import { cn } from "@/lib/cn";
import { ACTIVE_ETF_BADGE_COLOR, SYMBOL_TYPE_BADGE } from "@/lib/etf-badges";
import { isEtfSymbolType } from "@/lib/symbol-type";
import type { EtfListRow, ScoreBand, SymbolType } from "@/types/market";
import type { Market } from "@/types/settings";

const HISTORY_DAYS_FOR_CHANGE = 2;

const BAND_TOKEN: Record<ScoreBand, string> = {
  偏多: "var(--up)",
  中性: "var(--text-muted)",
  偏空: "var(--down)",
};

/**
 * The stock-detail page's sticky header (`docs/web/stock-detail.md` §1/§7): name+code+sector,
 * big tabular price + ▲▼%+sign (紅漲綠跌, never color-alone), 開/高/低/量 micro-row, the `[偏多
 * 72]` band pill (click scrolls to 智能摘要's gauge anchor — built now even though that tab
 * itself ships partial in this phase). Name/industry come from `get_symbol_info`, not
 * `get_company_profile` — that tool's output has no name field at all (composed from
 * sector/dividends/market-value/valuation/financials/MOPS-profile capabilities, none of which
 * carry the company's display name). Price/change/OHLCV come from a direct `get_price_history`
 * call over a 2-day window. ☆/💬 are honest placeholders: no `/watchlists` write path exists yet
 * (`WatchlistRail` itself is still an unwired shell), so ☆ stays visually present but
 * non-interactive rather than faking a save; 💬 links to `/chat` plain (no seed-query mechanism
 * exists yet either).
 */
export function AssetHeader({
  symbol,
  market,
  symbolType = "stock",
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly symbolType?: SymbolType;
}): ReactNode {
  const t = useTranslations("stock.assetHeader");
  const locale = useLocale();
  const reduceMotion = useReducedMotion();
  const isEtf = isEtfSymbolType(symbolType);

  const symbolInfoQuery = useQuery({
    queryKey: ["symbol_info", symbol, market],
    queryFn: () => getSymbolInfo([symbol], market),
  });
  const symbolInfo = symbolInfoQuery.data?.rows[0];
  const etfFundInfoQuery = useQuery({
    queryKey: ["etf_fund_info", symbol, market],
    queryFn: () => getEtfFundInfo(symbol, market),
    enabled: isEtf,
  });
  const trackedIndex = etfFundInfoQuery.data?.info?.tracked_index;
  // `get_etf_list` is whole-market (no per-symbol input — see its own docstring), so this reads
  // the same list the market ETF tab already fetches and narrows to this one symbol client-side,
  // the same "fetch whole, filter locally" shape every other per-symbol slice of a whole-market
  // dataset in this codebase uses. TanStack dedupes this against the market tab's own identical
  // query key, so an ETF's own detail page doesn't cost a second distinct network request beyond
  // whatever's already cached.
  const etfListQuery = useQuery({
    queryKey: ["etf_list", market],
    queryFn: () => getEtfList(market),
    enabled: isEtf,
  });
  const etfListRow = etfListQuery.data?.data_available
    ? etfListQuery.data.rows.find((row) => row.symbol === symbol)
    : undefined;
  const distributionFrequency = etfListRow?.distribution_frequency;
  const themeTagsQuery = useQuery({
    queryKey: ["etf_theme_tags", symbol, market],
    queryFn: () => getEtfThemeTags(symbol, market),
    enabled: isEtf,
  });
  const themeTags = themeTagsQuery.data?.tags ?? [];
  const scoreQuery = useQuery({
    queryKey: ["stock_score", symbol, market],
    queryFn: () => getStockScore(symbol, market),
  });
  const historyQuery = useQuery({
    queryKey: ["price_history", symbol, market, HISTORY_DAYS_FOR_CHANGE],
    queryFn: () => getPriceHistory([symbol], market, HISTORY_DAYS_FOR_CHANGE),
  });
  const bars = [...(historyQuery.data?.rows ?? [])].sort((a, b) =>
    a.trade_date.localeCompare(b.trade_date),
  );
  const latestBar = bars.at(-1) ?? null;
  const priorBar = bars.length >= 2 ? bars.at(-2) : null;
  const changePct =
    latestBar && priorBar && priorBar.close !== 0
      ? (latestBar.close / priorBar.close - 1) * 100
      : null;
  const isUp = changePct !== null && changePct >= 0;
  const priceColor = changePct === null ? "var(--text)" : isUp ? "var(--up)" : "var(--down)";

  const band = scoreQuery.data?.found ? scoreQuery.data.result?.band : undefined;
  const composite = scoreQuery.data?.found ? scoreQuery.data.result?.composite : undefined;

  return (
    <header className="sticky top-0 z-10 border-[var(--border)] border-b bg-[var(--surface)] px-5 py-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-2">
          <h1 className="truncate font-semibold text-[20px] text-[var(--text)]">
            {symbolInfo?.name ?? symbol}
          </h1>
          <span className="font-num text-[15px] text-[var(--text-muted)] tabular-nums">
            {symbol}
          </span>
          {headerChipLabel({
            isEtf,
            trackedIndex,
            industryCategory: symbolInfo?.industry_category,
          })}
          {isEtf ? <EtfStructureBadges row={etfListRow} /> : null}
          {distributionFrequency ? (
            <DistributionFrequencyBadge frequency={distributionFrequency} />
          ) : null}
          {themeTags.map((tag) => (
            <ThemeTagBadge key={tag} tag={tag} />
          ))}
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          <button
            type="button"
            disabled
            title={t("watchlistComingSoon")}
            aria-label={t("addToWatchlist")}
            className="inline-flex size-11 items-center justify-center rounded-[var(--radius-button)] text-[var(--text-muted)] opacity-50"
          >
            <StarIcon size={18} weight="regular" aria-hidden />
          </button>
          <Link
            href="/chat"
            className="inline-flex min-h-11 items-center gap-1.5 rounded-[var(--radius-button)] px-3 text-[13px] text-[var(--accent-text)] transition-colors hover:bg-[var(--surface-2)]"
          >
            <motion.span
              aria-hidden
              className="inline-flex"
              animate={reduceMotion ? undefined : { rotate: [0, 15, -10, 0], scale: [1, 1.15, 1] }}
              transition={
                reduceMotion
                  ? undefined
                  : { duration: 2.4, repeat: Number.POSITIVE_INFINITY, ease: "easeInOut" }
              }
            >
              <SparkleIcon size={16} weight="fill" />
            </motion.span>
            {t("askMiru")}
          </Link>
        </div>
      </div>

      <div className="mt-2 flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span
          role="group"
          aria-label={
            changePct !== null
              ? `${formatNumber(latestBar?.close ?? null, locale)} ${isUp ? "上漲" : "下跌"} ${Math.abs(changePct).toFixed(2)}%`
              : formatNumber(latestBar?.close ?? null, locale)
          }
          className="flex items-baseline gap-2"
        >
          <span
            aria-hidden
            className="font-num text-[28px] tabular-nums"
            style={{ color: priceColor }}
          >
            {formatNumber(latestBar?.close ?? null, locale, { decimals: 2 })}
          </span>
          {changePct !== null ? (
            <span
              aria-hidden
              className="font-num text-[15px] tabular-nums"
              style={{ color: priceColor }}
            >
              {isUp ? "▲" : "▼"}
              {formatNumber(Math.abs(changePct), locale, { decimals: 2 })}%
            </span>
          ) : null}
        </span>

        {band ? (
          <a
            href="#stock-score-gauge"
            className="rounded-[var(--radius-pill)] px-2.5 py-1 text-[12px] font-medium transition-opacity hover:opacity-80"
            style={{ backgroundColor: `${BAND_TOKEN[band]}1F`, color: BAND_TOKEN[band] }}
          >
            {band}
            {composite != null ? ` ${composite.toFixed(0)}` : ""}
          </a>
        ) : null}
      </div>

      {/* 開/高/低/量 are neutral OHLC facts, not signed price/%/score deltas — categorical
          `--chart-1..4` per label (never `--up`/`--down`) just to visually separate the 4
          labels from each other, web/CLAUDE.md §4. */}
      <p className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-num text-[12px] tabular-nums">
        {latestBar ? (
          <>
            <span>
              <span style={{ color: "var(--chart-1)" }}>{t("open")} </span>
              <span className="text-[var(--text)]">
                {formatNumber(latestBar.open, locale, { decimals: 2 })}
              </span>
            </span>
            <span>
              <span style={{ color: "var(--chart-2)" }}>{t("high")} </span>
              <span className="text-[var(--text)]">
                {formatNumber(latestBar.high, locale, { decimals: 2 })}
              </span>
            </span>
            <span>
              <span style={{ color: "var(--chart-3)" }}>{t("low")} </span>
              <span className="text-[var(--text)]">
                {formatNumber(latestBar.low, locale, { decimals: 2 })}
              </span>
            </span>
            <span>
              <span style={{ color: "var(--chart-4)" }}>{t("volume")} </span>
              <span className="text-[var(--text)]">{formatNumber(latestBar.volume, locale)}</span>
            </span>
          </>
        ) : null}
        <span
          className={cn(
            "inline-flex items-center gap-1",
            historyQuery.isError && "text-[var(--warn)]",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "size-1.5 rounded-full",
              historyQuery.isError ? "bg-[var(--warn)]" : "bg-[var(--text-muted)]",
            )}
          />
          {historyQuery.isError ? t("dataDelayed") : t("eodOnly")}
        </span>
      </p>
    </header>
  );
}

/** ETFs have no sector; the same chip slot instead shows the tracked index (追蹤指數) when the
 * fund-info fetch has resolved one — a stock keeps the industry-category sector chip. */
function headerChipLabel({
  isEtf,
  trackedIndex,
  industryCategory,
}: {
  readonly isEtf: boolean;
  readonly trackedIndex: string | null | undefined;
  readonly industryCategory: string | null | undefined;
}): ReactNode {
  const label = isEtf ? trackedIndex : industryCategory;
  return label ? <SectorChip label={label} /> : null;
}

/** 原型/槓桿型/反向型 (`symbol_type`) + 主動式 (`is_active_etf`) badges — the SAME constants and
 * labels `components/market/etf-list-table.tsx` uses for the market ETF-tab list
 * (`lib/etf-badges.ts`), so a fund reads identically on both pages. Reads the
 * `market.cards.etfList` namespace directly (not `stock.assetHeader`) rather than duplicating
 * the label strings into a second namespace. `row` is `undefined` while `get_etf_list` is still
 * loading or this symbol isn't in the whole-market list yet — renders nothing rather than a
 * loading placeholder, matching `headerChipLabel`'s own "absent → no chip" precedent. */
function EtfStructureBadges({ row }: { readonly row: EtfListRow | undefined }): ReactNode {
  const t = useTranslations("market.cards.etfList");
  if (!row) return null;
  const symbolTypeBadge = SYMBOL_TYPE_BADGE[row.symbol_type];
  return (
    <>
      {row.is_active_etf ? (
        <span
          className="rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2.5 py-1 font-medium text-[12px]"
          style={{ color: ACTIVE_ETF_BADGE_COLOR }}
        >
          {t("active")}
        </span>
      ) : null}
      {symbolTypeBadge ? (
        <span
          className="rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2.5 py-1 font-medium text-[12px]"
          style={{ color: symbolTypeBadge.color }}
        >
          {t(symbolTypeBadge.labelKey)}
        </span>
      ) : null}
    </>
  );
}

/** 主題/因子 badge — TWSE's own ETF e添富 editorial tag (高股息/科技主題型/公司治理及ESG/...),
 * colored by the tag LABEL itself via `hashToChartToken` (the same stable categorical-color hash
 * `SectorChip`/`DistributionFrequencyBadge` use), never signed `--up`/`--down`: this is a
 * classification, not a price/%/score, per web/CLAUDE.md §4. */
function ThemeTagBadge({ tag }: { readonly tag: string }): ReactNode {
  const token = hashToChartToken(tag);
  return (
    <span
      className="rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px]"
      style={{
        backgroundColor: `${token}26`,
        color: token,
        border: `1px solid ${token}40`,
      }}
    >
      {tag}
    </span>
  );
}

/** 配息 cadence badge (月配/季配/半年配/年配/不配息) — colored by the frequency LABEL itself via
 * `hashToChartToken` (the same stable categorical-color hash `SectorChip` uses), never signed
 * `--up`/`--down`: this is a cadence classification, not a price/%/score, per web/CLAUDE.md §4.
 * A distinct color per frequency lets a reader tell 月配 from 年配 at a glance across a list of
 * ETF pages, the same reasoning behind giving each industry its own stable color in the holdings
 * donut/table. */
function DistributionFrequencyBadge({ frequency }: { readonly frequency: string }): ReactNode {
  const token = hashToChartToken(frequency);
  return (
    <span
      className="rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px]"
      style={{
        backgroundColor: `${token}26`,
        color: token,
        border: `1px solid ${token}40`,
      }}
    >
      {frequency}
    </span>
  );
}
