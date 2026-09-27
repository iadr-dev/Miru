"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { Sparkline } from "@/components/market/sparkline";
import { SymbolLink } from "@/components/market/symbol-link";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { cn } from "@/lib/cn";
import type { MarketRankingRow } from "@/types/market";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[1.3fr_0.8fr_0.8fr_0.9fr_0.9fr]";
type SortDirection = "asc" | "desc";

/** The 4 US-only 排行榜 tabs' shared row list (熱門美股/市值/營收/ROE) — every tab shares the
 * same 名稱/價格/漲跌%/走勢/成交量 column set (web/CLAUDE.md's "repeated inline shape → one
 * component" convention), unlike the old per-tab column set this replaced (漲跌/成交量 varied by
 * tab, no sparkline at all). `price`/`change_pct`/`volume` come straight from
 * `get_us_market_rankings`'s own row (a live Yahoo screener quote already carries these in one
 * call); `走勢` is the one field that screener quote doesn't carry, so this enriches via
 * `useSymbolQuotes` (`get_price_history`) for the sparkline only — price/change% still come
 * from the row's own real screener-quote fields, never overwritten by the quote's shorter
 * default window. 漲跌% is click-to-sort (▲/▼ on the active header), replacing the old
 * 漲幅/跌幅 pill toggle `RankingsCard` used to render above this table. */
export function UsMarketRankingRows({
  rows,
  market,
}: {
  readonly rows: readonly MarketRankingRow[];
  readonly market: Market;
}): ReactNode {
  const t = useTranslations("market.cards.rankings");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const { quotes } = useSymbolQuotes(
    rows.map((row) => row.symbol),
    market,
  );
  const quoteBySymbol = new Map(quotes.map((q) => [q.code, q]));

  const sortedRows = useMemo(() => {
    const sign = sortDirection === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      if (a.change_pct === null && b.change_pct === null) return 0;
      if (a.change_pct === null) return 1;
      if (b.change_pct === null) return -1;
      return sign * (a.change_pct - b.change_pct);
    });
  }, [rows, sortDirection]);

  const toggleSort = (): void => {
    setSortDirection((prev) => (prev === "asc" ? "desc" : "asc"));
  };

  return (
    <>
      <div
        className={cn(
          "grid items-center gap-2.5 border-[var(--border)] border-b pb-1.5",
          ROW_GRID_COLS,
        )}
      >
        <span className="text-[11px] text-[var(--text-muted)]">{t("columnName")}</span>
        <span className="text-right text-[11px] text-[var(--text-muted)]">{t("columnPrice")}</span>
        <div
          role="columnheader"
          aria-sort={sortDirection === "asc" ? "ascending" : "descending"}
          tabIndex={-1}
        >
          <button
            type="button"
            onClick={toggleSort}
            className="flex min-h-11 w-full items-center justify-end gap-1 text-[11px] text-[var(--text)] transition-colors hover:text-[var(--accent)]"
          >
            <span>{t("columnChangePct")}</span>
            <span aria-hidden className="text-[10px]">
              {sortDirection === "asc" ? "▲" : "▼"}
            </span>
          </button>
        </div>
        <span className="text-center text-[11px] text-[var(--text-muted)]">
          {t("columnSparkline")}
        </span>
        <span className="text-right text-[11px] text-[var(--text-muted)]">{t("columnVolume")}</span>
      </div>
      <ul className="divide-y divide-[var(--border)]">
        {sortedRows.map((row) => {
          const changePct = row.change_pct;
          const up = changePct !== null && changePct >= 0;
          const color =
            changePct !== null ? (up ? "var(--up)" : "var(--down)") : "var(--text-muted)";
          const name = row.name ?? row.symbol;
          const sparkline = quoteBySymbol.get(row.symbol)?.sparkline ?? [];
          return (
            <li
              key={row.symbol}
              role="group"
              aria-label={`${name} ${row.symbol} ${
                changePct !== null
                  ? `${up ? "▲" : "▼"} ${formatNumber(Math.abs(changePct), "zh-TW", { decimals: 2 })}%`
                  : t("empty")
              }`}
              className={cn("grid items-center gap-2.5 py-2.5", ROW_GRID_COLS)}
            >
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
                {row.price !== null ? formatNumber(row.price, "zh-TW", { decimals: 2 }) : "—"}
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[12.5px] tabular-nums"
                style={{ color }}
              >
                {changePct !== null
                  ? `${up ? "▲" : "▼"}${formatNumber(Math.abs(changePct), "zh-TW", { decimals: 2 })}%`
                  : "—"}
              </span>
              <span aria-hidden className="h-[26px]">
                <Sparkline values={sparkline} color={color} />
              </span>
              <span
                aria-hidden
                className="text-right font-num text-[12.5px] text-[var(--text-muted)] tabular-nums"
              >
                {row.volume !== null ? formatNumber(row.volume, "zh-TW", { decimals: 0 }) : "—"}
              </span>
            </li>
          );
        })}
      </ul>
    </>
  );
}
