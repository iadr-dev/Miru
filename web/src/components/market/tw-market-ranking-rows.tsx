"use client";

import type { ReactNode } from "react";
import { SymbolQuoteRows } from "@/components/market/symbol-quote-rows";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import type { TwRankingRow } from "@/types/market/rankings";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[1.3fr_0.8fr_0.8fr_0.9fr_0.9fr_1fr_28px]";

/** The trailing metric cell, coloured by the sign of `extra_value`. A plain module-level render
 * function (`SymbolQuoteRows` calls `extraCell(row)` inline, it is not mounted as `<Component/>`). */
function renderExtraCell(row: Pick<TwRankingRow, "extra_value" | "extra_label">): ReactNode {
  return (
    <span
      aria-hidden
      className="text-right font-num text-[11.5px] tabular-nums"
      style={{ color: row.extra_value >= 0 ? "var(--up)" : "var(--down)" }}
    >
      {row.extra_label}
    </span>
  );
}

/** The 6 non-大戶買賣 TW-market 排行榜 tabs' shared row list (主力/投信/外資連買賣、融資增減、
 * 股息殖利率、月營收成長) — built on `SymbolQuoteRows`' shared 名稱/價格/漲跌%/走勢/成交量 base,
 * mirroring `UsMarketRankingRows`' own shape, plus one trailing extra column carrying each tab's
 * own distinct metric (`TwRankingRow.extra_value`/`extra_label`, already formatted server-side —
 * see `tools/tw_ranking_tools.py::TwRankingRow`'s own docstring for why one shared numeric field
 * covers all 6 kinds). The extra column is click-to-sort too (`SymbolQuoteRows`' `extraSortable`
 * prop, same convention as 漲跌%/成交量) — the primary metric each of these 6 tabs ranks by, so
 * it needs to sort just as much as the shared columns do. This tool's row has no live quote of
 * its own (no screener backs it, unlike the US version), so price/change_pct/volume are always
 * enriched via `useSymbolQuotes` (`get_price_history`), never overridden. */
export function TwMarketRankingRows({
  rows,
  market,
  extraColumnLabel,
}: {
  readonly rows: readonly TwRankingRow[];
  readonly market: Market;
  /** This tab's own extra-column header (連買賣天數/融資變動/殖利率/營收年增率) — the caller
   * decides the label per active `TwRankingKind`, this component only renders the shared layout. */
  readonly extraColumnLabel: string;
}): ReactNode {
  const { quotes } = useSymbolQuotes(
    rows.map((row) => row.symbol),
    market,
  );
  const quoteBySymbol = new Map(quotes.map((q) => [q.code, q]));
  const items = rows.map((row) => ({ ...row, name: row.name ?? undefined }));

  return (
    <SymbolQuoteRows
      rows={items}
      quoteBySymbol={quoteBySymbol}
      market={market}
      assetClass="stock"
      gridCols={ROW_GRID_COLS}
      extraSortable={{ label: extraColumnLabel, valueFor: (row) => row.extra_value }}
      extraAriaLabel={(row) => row.extra_label}
      extraCell={renderExtraCell}
    />
  );
}
