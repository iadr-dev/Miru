"use client";

/**
 * The shared row model for "one row per stock/ETF symbol" surfaces (the AI leaderboard, a
 * watchlist, an ETF roster, rankings, the screener): `{code, name, price, changePct, sparkline,
 * volume}`.
 *
 * Backed by one batched (≤20 symbols) backend route, `GET /market/quotes`
 * (`get_symbol_quotes`), which joins the roster name with the price window and computes the
 * change server-side. `changePct` is therefore always the DAILY move (latest close vs. the
 * previous trading day), whatever window the sparkline spans: the same number the stock header
 * and every list show. Combining `symbol-info` + `price-history` in the browser
 * used to let each caller pick its own window, so a 20-day move appeared under 漲跌%.
 *
 * Every field is honestly nullable: a symbol absent from the roster gets `name: null`; a symbol
 * with fewer than 2 bars gets `changePct: null` and a short `sparkline`, never a fabricated value.
 */
import { useQueries } from "@tanstack/react-query";
import { useMemo } from "react";
import { getSymbolQuotes } from "@/lib/api/market";
import type { SymbolQuoteRow } from "@/types/market/quotes";
import type { Market } from "@/types/settings";

export type SymbolQuote = {
  code: string;
  /** `null` when the symbol isn't in the current whole-market roster — never a placeholder. */
  name: string | null;
  industryCategory: string | null;
  /** Latest close, or `null` if no bar was found. */
  price: number | null;
  /** Daily change %, latest close vs. the previous trading day's close (computed by the
   * backend). `null` when fewer than 2 bars exist. */
  changePct: number | null;
  /** Ascending-by-date closes over the requested window — feeds a sparkline directly. */
  sparkline: readonly number[];
  /** The latest bar's volume, or `null` if no bar was found. */
  volume: number | null;
};

const DEFAULT_SPARKLINE_DAYS = 20;
const MAX_BATCH_SYMBOLS = 20; // the backend's per-call bound

function chunk<T>(items: readonly T[], size: number): T[][] {
  const chunks: T[][] = [];
  for (let i = 0; i < items.length; i += size) {
    chunks.push(items.slice(i, i + size));
  }
  return chunks;
}

function toQuote(code: string, row: SymbolQuoteRow | undefined): SymbolQuote {
  return {
    code,
    name: row?.name ?? null,
    industryCategory: row?.industry_category ?? null,
    price: row?.close ?? null,
    changePct: row?.change_pct ?? null,
    sparkline: row?.sparkline ?? [],
    volume: row?.volume ?? null,
  };
}

/**
 * Returns one `SymbolQuote` per requested symbol, in order, from one request per 20 symbols.
 * Call it ONCE per list with every symbol in it, never once per row: a per-row call turns a
 * 40-row list into 40 requests that queue behind the browser's per-origin connection limit.
 * `days` sets only the sparkline window; the change is always daily.
 */
export function useSymbolQuotes(
  symbols: readonly string[],
  market: Market,
  days = DEFAULT_SPARKLINE_DAYS,
): { quotes: SymbolQuote[]; isLoading: boolean; isError: boolean } {
  const batches = chunk(symbols, MAX_BATCH_SYMBOLS);

  const results = useQueries({
    queries: batches.map((batch) => ({
      queryKey: ["symbol_quotes", market, days, ...batch],
      queryFn: () => getSymbolQuotes(batch, market, days),
      enabled: batch.length > 0,
    })),
  });

  // `useMemo` deps must stay a fixed length, but the batch count varies with `symbols.length`,
  // so fold the inputs into stable fingerprints: the symbol list, and each batch's last update
  // time (which changes whenever its data does). Without this, `quotes` would be a new array
  // every render and defeat downstream memoization (e.g. the screener's TanStack Table).
  const symbolsKey = symbols.join(",");
  const dataKey = results.map((q) => q.dataUpdatedAt).join(",");
  // biome-ignore lint/correctness/useExhaustiveDependencies: keyed on the fingerprints above
  const quotes = useMemo(() => {
    const rowBySymbol = new Map<string, SymbolQuoteRow>();
    for (const result of results) {
      for (const row of result.data?.rows ?? []) {
        rowBySymbol.set(row.symbol, row);
      }
    }
    return symbols.map((code) => toQuote(code, rowBySymbol.get(code)));
  }, [symbolsKey, dataKey]);

  return {
    quotes,
    isLoading: results.some((q) => q.isLoading),
    isError: results.some((q) => q.isError),
  };
}
