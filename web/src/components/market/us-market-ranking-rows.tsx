"use client";

import type { ReactNode } from "react";
import { SymbolQuoteRows } from "@/components/market/symbol-quote-rows";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import type { MarketRankingRow } from "@/types/market/rankings";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[1.3fr_0.8fr_0.8fr_0.9fr_0.9fr_28px]";

/** The 4 US-only 排行榜 tabs' shared row list (熱門美股/市值/營收/ROE) — built on
 * `SymbolQuoteRows`' shared 名稱/價格/漲跌%/走勢/成交量 base (no rank column, no extra column).
 * `price`/`change_pct`/`volume` come straight from `get_us_market_rankings`'s own row (a live
 * Yahoo screener quote already carries these in one call) via `priceFor`/`changePctFor`/
 * `volumeFor` — all three are overridden, not just change/volume: `useSymbolQuotes`'s own
 * `get_price_history` fetch can legitimately have no recent daily bar for an illiquid/OTC-ish
 * ticker (confirmed live: several ROE-ranking rows showed a fabricated-looking "—" price before
 * `priceFor` was wired here, despite the row's own screener quote carrying a real one). `走勢` is
 * the one field the screener quote doesn't carry at all, so that alone still comes from
 * `useSymbolQuotes`'s sparkline. 漲跌%/成交量 click-to-sort is `SymbolQuoteRows`' own built-in
 * behavior. */
export function UsMarketRankingRows({
  rows,
  market,
}: {
  readonly rows: readonly MarketRankingRow[];
  readonly market: Market;
}): ReactNode {
  const { quotes } = useSymbolQuotes(
    rows.map((row) => row.symbol),
    market,
  );
  const quoteBySymbol = new Map(quotes.map((q) => [q.code, q]));
  // `MarketRankingRow.name` is `string | null` (honestly nullable — a screener quote missing a
  // display name); `SymbolQuoteRowItem.name` is `string | undefined` (optional-if-absent). `??
  // undefined` bridges the two without fabricating a name — `SymbolQuoteRows` itself falls back
  // to the quote's own roster name, then the bare symbol, when `name` is absent either way.
  const items = rows.map((row) => ({ ...row, name: row.name ?? undefined }));

  return (
    <SymbolQuoteRows
      rows={items}
      quoteBySymbol={quoteBySymbol}
      market={market}
      assetClass="stock"
      gridCols={ROW_GRID_COLS}
      priceFor={(row) => row.price}
      changePctFor={(row) => row.change_pct}
      volumeFor={(row) => row.volume}
    />
  );
}
