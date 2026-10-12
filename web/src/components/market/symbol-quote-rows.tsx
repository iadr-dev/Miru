"use client";

import { CaretDownIcon, CaretUpDownIcon, CaretUpIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { Sparkline } from "@/components/market/sparkline";
import { SymbolLink } from "@/components/market/symbol-link";
import { WatchlistStarButton } from "@/components/stock/watchlist-star-button";
import type { SymbolQuote } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type { Market } from "@/types/settings";
import type { WatchlistAssetClass } from "@/types/watchlist";

/** One row's identity — every "one row per stock/ETF symbol" list in this app names at least a
 * symbol; `name` is optional because some callers (`get_price_momentum`'s own ranking rows)
 * already carry a real name from their own source and should show that rather than
 * `useSymbolQuotes`' roster-derived one (both are real, but a caller's own source name is more
 * directly authoritative for what it's ranking by). */
export interface SymbolQuoteRowItem {
  readonly symbol: string;
  readonly name?: string;
}

type SortKey = "change" | "volume" | "extra";
type SortDirection = "asc" | "desc";

/**
 * The shared row-list for every "名稱+代號 / 價格 / 漲跌% / 走勢 / 成交量" surface — the base
 * shape `hooks/use-symbol-quotes.ts` already documents (AI 多空排行, 強勢股/弱勢股, and now any
 * future one-row-per-symbol list: an ETF roster, a sub-industry member list, a watchlist). Three
 * real callers (`LeaderboardCard`'s AI-score ranking, `PriceMomentumCard`'s pure %-change
 * ranking, `IndustryPanel`'s sub-industry member list) converged on byte-identical header/row
 * markup before this was extracted — differing only in one trailing column (leaderboard's own
 * AI-score pill) and which field supplies the sort-order %-change (a ranking tool's own real
 * return vs. the quote's own derived window). Both are expressed here as an optional
 * `extraColumn`/`changePctFor` rather than baked in, so a caller with no extra column at all
 * renders the exact same base with zero extra markup.
 *
 * 漲跌%/成交量 are click-to-sort (▲/▼ on the active header, `EtfListTable`'s own convention) —
 * sort state lives here since both columns are part of this shared base, not caller-owned.
 *
 * Ownership split: this component owns the shared 5 columns' markup, alignment, sort, and a11y
 * (`SymbolLink`/`Sparkline`/▲▼+sign+aria-label, `web/CLAUDE.md` §4); the caller owns its own
 * grid-column-count string (so a caller with an extra column, or a leading rank number, sizes its
 * own grid), its own header labels for anything beyond the shared 5, and its own row data. */
export function SymbolQuoteRows<T extends SymbolQuoteRowItem>({
  rows,
  quoteBySymbol,
  market,
  assetClass,
  gridCols,
  priceFor,
  changePctFor,
  volumeFor,
  showRank = false,
  extraHeader,
  extraCell,
  extraAriaLabel,
  extraSortable,
}: {
  readonly rows: readonly T[];
  readonly quoteBySymbol: ReadonlyMap<string, SymbolQuote>;
  readonly market: Market;
  /** Constant per caller (a TW/US ranking is always stocks, the ETF leaderboard is always ETFs)
   * — never varies per row, so this is a component-level prop, not derived per item. Drives the
   * trailing ☆ column's `WatchlistStarButton`. */
  readonly assetClass: WatchlistAssetClass;
  /** This row-list's own Tailwind `grid-cols-[...]` value — the caller decides how many/wide
   * columns exist (rank column, extra column), this component only fills in the shared 5. */
  readonly gridCols: string;
  /** Same override shape as `changePctFor`/`volumeFor`, for 價格 — supply this when the row's own
   * source already carries a real price (e.g. `get_us_market_rankings`' own live screener quote)
   * instead of falling back to the quote's derived `get_price_history` latest bar, which can be
   * null for a symbol that fetch doesn't cover (an illiquid/OTC-ish ticker with no recent daily
   * bar) even though the row's own source has a real price for it — confirmed live: several US
   * ROE-ranking rows (NTNX, PBT, MMYT, GEMI) showed a fabricated-looking "—" price this way
   * despite having real 漲跌%/成交量, before this override existed. */
  readonly priceFor?: (row: T) => number | null | undefined;
  /** `null`/`undefined` falls back to the quote's own derived `changePct` (the common case);
   * supply this when the row's own source already carries the authoritative %-change (e.g.
   * `get_price_momentum`'s real period return) instead of the shorter default quote window. */
  readonly changePctFor?: (row: T) => number | null | undefined;
  /** Same override shape as `changePctFor`, for 成交量 — supply this when the row's own source
   * already carries a real volume (e.g. `get_us_market_rankings`' own live screener quote)
   * instead of falling back to the quote's derived latest-bar volume. */
  readonly volumeFor?: (row: T) => number | null | undefined;
  /** Renders a leading `#index` cell before the name column — the leaderboard/momentum ranking
   * convention; a plain member list (no inherent rank) omits it. Sorting (漲跌%/成交量) still
   * applies on top of this rank label — the label itself is recomputed from the caller's own
   * `rows` order, not the sorted order, since `showRank` conventionally numbers the caller's own
   * ranking (e.g. AI 多空排行's score order), not this component's own transient sort. */
  readonly showRank?: boolean;
  /** Static (non-sortable) extra-column header — mutually exclusive with `extraSortable`. Most
   * callers (leaderboard's AI-score column, industry member lists) have no natural sort key of
   * their own; `extraSortable` below is for the ones that do (e.g. TW rankings' streak/margin/
   * yield/growth column). */
  readonly extraHeader?: ReactNode;
  readonly extraCell?: (row: T, quote: SymbolQuote | undefined) => ReactNode;
  /** Appended to the shared name/symbol/change aria-label — for a caller whose extra column
   * carries its own announcable value (e.g. the leaderboard's AI band + score). */
  readonly extraAriaLabel?: (row: T) => string;
  /** Makes the extra column click-to-sort, same convention as 漲跌%/成交量 — `label` is the
   * header text, `valueFor` extracts the numeric sort key from a row. Takes over rendering the
   * extra header (do not also pass `extraHeader` alongside this). */
  readonly extraSortable?: { label: string; valueFor: (row: T) => number | null };
}): ReactNode {
  const t = useTranslations("market.cards.leaderboard");
  const [sortKey, setSortKey] = useState<SortKey | null>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");

  const toggleSort = (key: SortKey): void => {
    if (key === sortKey) {
      setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
      return;
    }
    setSortKey(key);
    setSortDirection("desc");
  };

  const sortedRows = useMemo(() => {
    if (sortKey === null) return rows;
    const sign = sortDirection === "asc" ? 1 : -1;
    const valueFor = (row: T): number | null => {
      const quote = quoteBySymbol.get(row.symbol);
      if (sortKey === "change") {
        const pct = changePctFor ? changePctFor(row) : quote?.changePct;
        return pct ?? null;
      }
      if (sortKey === "volume") {
        const volume = volumeFor ? volumeFor(row) : quote?.volume;
        return volume ?? null;
      }
      return extraSortable ? extraSortable.valueFor(row) : null;
    };
    return [...rows].sort((a, b) => {
      const av = valueFor(a);
      const bv = valueFor(b);
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return sign * (av - bv);
    });
  }, [rows, sortKey, sortDirection, quoteBySymbol, changePctFor, volumeFor, extraSortable]);

  return (
    // Phones scroll the dense grid sideways at a readable minimum width instead of crushing the
    // name column to a sliver.
    <div className="-mx-1 overflow-x-auto px-1">
      <div className="min-w-[34rem] md:min-w-0">
        <div
          className={cn(
            "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]",
            gridCols,
          )}
        >
          {showRank ? <span aria-hidden /> : null}
          <span aria-hidden>{t("columnName")}</span>
          <span aria-hidden className="text-right">
            {t("columnPrice")}
          </span>
          <SortableHeaderCell
            label={t("columnChange")}
            active={sortKey === "change"}
            direction={sortDirection}
            onClick={() => toggleSort("change")}
          />
          <span aria-hidden className="text-center">
            {t("columnSparkline")}
          </span>
          <SortableHeaderCell
            label={t("columnVolume")}
            active={sortKey === "volume"}
            direction={sortDirection}
            onClick={() => toggleSort("volume")}
          />
          {extraSortable ? (
            <SortableHeaderCell
              label={extraSortable.label}
              active={sortKey === "extra"}
              direction={sortDirection}
              onClick={() => toggleSort("extra")}
            />
          ) : (
            extraHeader
          )}
          <span aria-hidden />
        </div>
        <ul className="divide-y divide-[var(--border)]">
          {sortedRows.map((row) => {
            const quote = quoteBySymbol.get(row.symbol);
            const pctChange = changePctFor ? changePctFor(row) : quote?.changePct;
            const hasChange = pctChange !== null && pctChange !== undefined;
            const up = hasChange && pctChange >= 0;
            const color = hasChange ? (up ? "var(--up)" : "var(--down)") : "var(--text-muted)";
            const name = row.name ?? quote?.name ?? row.symbol;
            const rank = showRank ? rows.indexOf(row) + 1 : null;
            const volume = volumeFor ? volumeFor(row) : quote?.volume;
            const price = priceFor ? priceFor(row) : quote?.price;
            return (
              <li
                key={row.symbol}
                role="group"
                aria-label={`${name} ${row.symbol} ${
                  hasChange
                    ? `${up ? "▲" : "▼"} ${formatNumber(Math.abs(pctChange), "zh-TW", { decimals: 2 })}%`
                    : t("empty")
                }${extraAriaLabel ? ` ${extraAriaLabel(row)}` : ""}`}
                className={cn("grid items-center gap-2.5 py-2.5", gridCols)}
              >
                {showRank ? (
                  <span aria-hidden className="text-[12px] text-[var(--text-muted)]">
                    {rank}
                  </span>
                ) : null}
                <SymbolLink
                  symbol={row.symbol}
                  market={market}
                  className="truncate text-[14px] text-[var(--text)] hover:underline"
                >
                  {name}
                  <span className="ml-1.5 text-[11.5px] text-[var(--text-muted)]">
                    {row.symbol}
                  </span>
                </SymbolLink>
                <span
                  aria-hidden
                  className="text-right font-num text-[13px] text-[var(--text)] tabular-nums"
                >
                  {price !== null && price !== undefined
                    ? formatNumber(price, "zh-TW", { decimals: 1 })
                    : "—"}
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[11.5px] tabular-nums"
                  style={{ color }}
                >
                  {hasChange
                    ? `${up ? "▲" : "▼"}${formatNumber(Math.abs(pctChange), "zh-TW", { decimals: 2 })}%`
                    : "—"}
                </span>
                <span aria-hidden className="h-[26px]">
                  <Sparkline values={quote?.sparkline ?? []} color={color} />
                </span>
                <span
                  aria-hidden
                  className="text-right font-num text-[11.5px] text-[var(--text-muted)] tabular-nums"
                >
                  {volume !== null && volume !== undefined
                    ? formatNumber(volume, "zh-TW", { decimals: 0 })
                    : "—"}
                </span>
                {extraCell ? extraCell(row, quote) : null}
                <WatchlistStarButton
                  symbol={row.symbol}
                  market={market}
                  assetClass={assetClass}
                  size="compact"
                />
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

/** One clickable, sortable column header — a caret icon sits to the LEFT of the label (the
 * common site convention the reference screenshot for this feature showed: an up/down glyph
 * marking "this column sorts", not trailing the text). Unsorted state shows a neutral up+down
 * caret (`CaretUpDownIcon`) so a caller can tell the column IS sortable before ever clicking it;
 * the active column swaps to a single filled `CaretUpIcon`/`CaretDownIcon` matching the current
 * direction. `aria-sort` set on a `role="columnheader"` wrapper (not a real `<table>`, this is a
 * CSS grid), matching `EtfListTable`'s own established sortable-header convention (that one still
 * uses a trailing ▲/▼ glyph — this component's own header cell, not a shared one, so the two are
 * free to diverge here without touching that table). */
function SortableHeaderCell({
  label,
  active,
  direction,
  onClick,
}: {
  readonly label: string;
  readonly active: boolean;
  readonly direction: SortDirection;
  readonly onClick: () => void;
}): ReactNode {
  const CaretIcon = active ? (direction === "asc" ? CaretUpIcon : CaretDownIcon) : CaretUpDownIcon;
  return (
    <div
      role="columnheader"
      aria-sort={active ? (direction === "asc" ? "ascending" : "descending") : "none"}
      tabIndex={-1}
    >
      <button
        type="button"
        onClick={onClick}
        className={cn(
          "flex min-h-11 w-full items-center justify-end gap-1 text-[11px] transition-colors hover:text-[var(--accent)]",
          active ? "text-[var(--text)]" : "text-[var(--text-muted)]",
        )}
      >
        <CaretIcon size={11} weight="bold" aria-hidden />
        <span>{label}</span>
      </button>
    </div>
  );
}
