"use client";

import type { ReactNode } from "react";
import { SymbolQuoteRows } from "@/components/market/symbol-quote-rows";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import type { MarketRankingRow } from "@/types/market";
import type { Market } from "@/types/settings";

const ROW_GRID_COLS = "grid-cols-[1.3fr_0.8fr_0.8fr_0.9fr_0.9fr]";

/** The 4 US-only 排行榜 tabs' shared row list (熱門美股/市值/營收/ROE) — built on
 * `SymbolQuoteRows`' shared 名稱/價格/漲跌%/走勢/成交量 base (no rank column, no extra column).
 * `price`/`change_pct`/`volume` come straight from `get_us_market_rankings`'s own row (a live
 * Yahoo screener quote already carries these in one call) via `changePctFor`/`volumeFor`,
 * mirroring `PriceMomentumRows`' own `changePctFor` override; `走勢` is the one field that
 * screener quote doesn't carry, so this enriches via `useSymbolQuotes` (`get_price_history`) for
 * the sparkline only. 漲跌%/成交量 click-to-sort is `SymbolQuoteRows`' own built-in behavior. */
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
      gridCols={ROW_GRID_COLS}
      changePctFor={(row) => row.change_pct}
      volumeFor={(row) => row.volume}
    />
  );
}
