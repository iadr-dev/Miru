"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { Sparkline } from "@/components/market/sparkline";
import { SymbolLink } from "@/components/market/symbol-link";
import type { SymbolQuote } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

/** One row's identity — every "one row per stock/ETF symbol" list in this app names at least a
 * symbol; `name` is optional because some callers (`get_price_momentum`'s own ranking rows)
 * already carry a real name from their own source and should show that rather than
 * `useSymbolQuotes`' roster-derived one (both are real, but a caller's own source name is more
 * directly authoritative for what it's ranking by). */
export interface SymbolQuoteRowItem {
  readonly symbol: string;
  readonly name?: string;
}

/**
 * The shared row-list for every "名稱+代號 / 價格 / 漲跌% / 走勢" surface — the base shape
 * `hooks/use-symbol-quotes.ts` already documents (AI 多空排行, 強勢股/弱勢股, and now any future
 * one-row-per-symbol list: an ETF roster, a sub-industry member list, a watchlist). Two real
 * callers (`LeaderboardCard`'s AI-score ranking, `PriceMomentumCard`'s pure %-change ranking)
 * converged on byte-identical header/row markup before this was extracted — differing only in
 * one trailing column (leaderboard's own AI-score pill) and which field supplies the sort-order
 * %-change (a ranking tool's own real return vs. the quote's own derived window). Both are
 * expressed here as an optional `extraColumn`/`changePctFor` rather than baked in, so a THIRD
 * caller with no extra column at all (e.g. a plain sub-industry member list) renders the exact
 * same base with zero extra markup.
 *
 * Ownership split: this component owns the shared 4 columns' markup, alignment, and a11y
 * (`SymbolLink`/`Sparkline`/▲▼+sign+aria-label, `web/CLAUDE.md` §4); the caller owns its own
 * grid-column-count string (so a caller with an extra column, or a leading rank number, sizes its
 * own grid), its own header labels for anything beyond the shared 4, and its own row data. */
export function SymbolQuoteRows<T extends SymbolQuoteRowItem>({
  rows,
  quoteBySymbol,
  market,
  gridCols,
  changePctFor,
  showRank = false,
  extraHeader,
  extraCell,
  extraAriaLabel,
}: {
  readonly rows: readonly T[];
  readonly quoteBySymbol: ReadonlyMap<string, SymbolQuote>;
  readonly market: Market;
  /** This row-list's own Tailwind `grid-cols-[...]` value — the caller decides how many/wide
   * columns exist (rank column, extra column), this component only fills in the shared 4. */
  readonly gridCols: string;
  /** `null`/`undefined` falls back to the quote's own derived `changePct` (the common case);
   * supply this when the row's own source already carries the authoritative %-change (e.g.
   * `get_price_momentum`'s real period return) instead of the shorter default quote window. */
  readonly changePctFor?: (row: T) => number | null | undefined;
  /** Renders a leading `#index` cell before the name column — the leaderboard/momentum ranking
   * convention; a plain member list (no inherent rank) omits it. */
  readonly showRank?: boolean;
  readonly extraHeader?: ReactNode;
  readonly extraCell?: (row: T, quote: SymbolQuote | undefined) => ReactNode;
  /** Appended to the shared name/symbol/change aria-label — for a caller whose extra column
   * carries its own announcable value (e.g. the leaderboard's AI band + score). */
  readonly extraAriaLabel?: (row: T) => string;
}): ReactNode {
  const t = useTranslations("market.cards.leaderboard");

  return (
    <>
      <div
        aria-hidden
        className={cn(
          "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5 text-[11px] text-[var(--text-muted)]",
          gridCols,
        )}
      >
        {showRank ? <span /> : null}
        <span>{t("columnName")}</span>
        <span className="text-right">{t("columnPrice")}</span>
        <span className="text-right">{t("columnChange")}</span>
        <span className="text-center">{t("columnSparkline")}</span>
        {extraHeader}
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {rows.map((row, index) => {
          const quote = quoteBySymbol.get(row.symbol);
          const pctChange = changePctFor ? changePctFor(row) : quote?.changePct;
          const hasChange = pctChange !== null && pctChange !== undefined;
          const up = hasChange && pctChange >= 0;
          const color = hasChange ? (up ? "var(--up)" : "var(--down)") : "var(--text-muted)";
          const name = row.name ?? quote?.name ?? row.symbol;
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
                  {index + 1}
                </span>
              ) : null}
              <SymbolLink
                symbol={row.symbol}
                market={market}
                className="truncate text-[14px] text-[var(--text)] hover:underline"
              >
                {name}
                <span className="ml-1.5 text-[11.5px] text-[var(--text-muted)]">{row.symbol}</span>
              </SymbolLink>
              <span
                aria-hidden
                className="text-right font-num text-[13px] text-[var(--text)] tabular-nums"
              >
                {quote?.price !== null && quote?.price !== undefined
                  ? formatNumber(quote.price, "zh-TW", { decimals: 1 })
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
              {extraCell ? extraCell(row, quote) : null}
            </li>
          );
        })}
      </ul>
    </>
  );
}
