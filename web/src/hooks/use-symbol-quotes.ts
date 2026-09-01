"use client";

/**
 * The shared base raw-data model for "one row per stock/ETF symbol" surfaces (the AI
 * leaderboard, a watchlist, an ETF roster, …): `{code, name, price, changePct, sparkline}`.
 * Combines two real, batched (≤20 symbols) backend routes —
 * `GET /market/symbol-info` (name/industry) and `GET /market/price-history` (bounded OHLCV
 * window) — into one typed shape per symbol, so every caller enriches a bare symbol list the
 * same way instead of re-deriving name/price/chg%/sparkline logic per screen.
 *
 * Every field is honestly nullable: a symbol absent from `symbol-info`'s roster (delisted,
 * mistyped) gets `name: null`; a symbol with fewer than 2 price-history bars gets
 * `changePct: null` and an empty `sparkline` — never a fabricated name or an interpolated price.
 * `changePct` is computed here (first vs. last close in the fetched window), not asserted by the
 * backend — see `PriceHistoryBar`'s own docstring for why that tool stays a pure data read.
 */
import { useQueries } from "@tanstack/react-query";
import { getPriceHistory, getSymbolInfo } from "@/lib/api/market";
import type { PriceHistoryBar, SymbolInfoRow } from "@/types/market";
import type { Market } from "@/types/settings";

export type SymbolQuote = {
  code: string;
  /** `null` when the symbol isn't in the current whole-market roster — never a placeholder. */
  name: string | null;
  industryCategory: string | null;
  /** Latest close in the fetched history window, or `null` if no bar was found. */
  price: number | null;
  /** `(latest close / earliest close - 1) * 100` over the fetched window — `null` when fewer
   * than 2 bars exist for this symbol (nothing to compare). NOT a single-day change unless the
   * caller requested a 2-day window; callers needing "today vs. yesterday" should pass a small
   * `days` value to `useSymbolQuotes`. */
  changePct: number | null;
  /** Ascending-by-date closes over the fetched window — feeds a sparkline directly. Empty when
   * no bars were found (never a single synthetic point). */
  sparkline: readonly number[];
};

const DEFAULT_HISTORY_DAYS = 20;
const MAX_BATCH_SYMBOLS = 20; // mirrors lib/api/market.ts's own bound on the backend's ≤20 guardrail

/** Splits an arbitrarily long symbol list into ≤20-symbol chunks — callers (e.g. a leaderboard's
 * expanded 50-row view) may exceed the backend's per-call cap, so this hook batches internally
 * rather than pushing that bound onto every caller. */
function chunk<T>(items: readonly T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

function buildQuotes(
  symbols: readonly string[],
  infoRows: readonly SymbolInfoRow[] | undefined,
  historyBars: readonly PriceHistoryBar[] | undefined,
): SymbolQuote[] {
  const infoBySymbol = new Map((infoRows ?? []).map((row) => [row.symbol, row]));
  const barsBySymbol = new Map<string, PriceHistoryBar[]>();
  for (const bar of historyBars ?? []) {
    const existing = barsBySymbol.get(bar.symbol);
    if (existing) {
      existing.push(bar);
    } else {
      barsBySymbol.set(bar.symbol, [bar]);
    }
  }

  return symbols.map((code) => {
    const info = infoBySymbol.get(code);
    const bars = (barsBySymbol.get(code) ?? [])
      .slice()
      .sort((a, b) => a.trade_date.localeCompare(b.trade_date));
    const first = bars[0];
    const last = bars.at(-1);
    const changePct =
      bars.length >= 2 && first && last && first.close !== 0
        ? (last.close / first.close - 1) * 100
        : null;
    return {
      code,
      name: info?.name ?? null,
      industryCategory: info?.industry_category ?? null,
      price: last?.close ?? null,
      changePct,
      sparkline: bars.map((bar) => bar.close),
    };
  });
}

/**
 * Fetches and merges `symbol-info` + `price-history` for up to 20 symbols in two batched calls
 * (never one call per symbol) and returns one `SymbolQuote` per requested symbol, in the same
 * order. `enabled: symbols.length > 0` — an empty list issues no request rather than a
 * guaranteed-422 round-trip.
 */
export function useSymbolQuotes(
  symbols: readonly string[],
  market: Market,
  days = DEFAULT_HISTORY_DAYS,
): { quotes: SymbolQuote[]; isLoading: boolean; isError: boolean } {
  const batches = chunk(symbols, MAX_BATCH_SYMBOLS);

  const infoQueries = useQueries({
    queries: batches.map((batch) => ({
      queryKey: ["symbol_info", market, ...batch],
      queryFn: () => getSymbolInfo(batch, market),
      enabled: batch.length > 0,
    })),
  });
  const historyQueries = useQueries({
    queries: batches.map((batch) => ({
      queryKey: ["price_history", market, days, ...batch],
      queryFn: () => getPriceHistory(batch, market, days),
      enabled: batch.length > 0,
    })),
  });

  // Not memoized: `useMemo`'s dependency array must stay a stable length across renders, but the
  // number of ≤20-symbol batches (and thus how many query results feed this) varies with
  // `symbols.length` (e.g. a leaderboard's inline 10-row preview vs. its expanded 50-row modal).
  // The computation itself is a cheap flatMap + map over at most 50 rows — recomputing it every
  // render is far simpler than threading a variable-length dependency list through `useMemo`.
  const infoRows = infoQueries.flatMap((q) => q.data?.rows ?? []);
  const historyRows = historyQueries.flatMap((q) => q.data?.rows ?? []);
  const quotes = buildQuotes(symbols, infoRows, historyRows);

  return {
    quotes,
    isLoading: infoQueries.some((q) => q.isLoading) || historyQueries.some((q) => q.isLoading),
    isError: infoQueries.some((q) => q.isError) || historyQueries.some((q) => q.isError),
  };
}
