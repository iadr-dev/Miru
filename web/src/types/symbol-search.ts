/**
 * `GET /search` contracts — mirrors `backend/src/api/routers/context.py`'s `symbol_search`
 * route, which dispatches to one of two distinct tools depending on `market`:
 * - `market=TW` → `get_symbol_directory_search` (`GetSymbolDirectorySearchOutput`,
 *   `backend/src/tools/market/`) — a scan over the ingested `TaiwanStockInfo` roster.
 * - `market=US/GB/JP/EU` → `get_symbol_search` (`GetSymbolSearchOutput`,
 *   `backend/src/tools/analyst_tools.py`) — `yfinance.Search`, live per request.
 *
 * These two response shapes are genuinely different (real, distinct sources — see the backend
 * route's own docstring for why they're never merged into one envelope). `toSearchHit` below is
 * the one place that normalizes both into the single `SearchHit` shape the palette UI renders.
 */
import { z } from "zod";

export const twSymbolSearchResultSchema = z.object({
  symbol: z.string(),
  name: z.string(),
  instrument_kind: z.enum(["stock", "etf"]),
  industry_category: z.string().nullable().optional(),
});
export type TwSymbolSearchResult = z.infer<typeof twSymbolSearchResultSchema>;

export const twSymbolSearchResponseSchema = z.object({
  market: z.string(),
  roster_date: z.string().nullable().optional(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(twSymbolSearchResultSchema).default([]),
});
export type TwSymbolSearchResponse = z.infer<typeof twSymbolSearchResponseSchema>;

export const usSymbolSearchResultSchema = z.object({
  query: z.string(),
  symbol: z.string(),
  exchange: z.string(),
  short_name: z.string().nullable().optional(),
  long_name: z.string().nullable().optional(),
  quote_type: z.string().nullable().optional(),
  sector: z.string().nullable().optional(),
  industry: z.string().nullable().optional(),
  score: z.number().nullable().optional(),
});
export type UsSymbolSearchResult = z.infer<typeof usSymbolSearchResultSchema>;

export const usSymbolSearchResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: z.string(),
  results: z.array(usSymbolSearchResultSchema).default([]),
});
export type UsSymbolSearchResponse = z.infer<typeof usSymbolSearchResponseSchema>;

/** The one normalized shape the search palette actually renders — built by `toSearchHit`,
 * never a third backend contract. `market` stays on each hit (rather than being implied by
 * which list it came from) so a flattened, re-sorted result list can still build the right
 * `/stock/{symbol}?market=` link. */
export type SearchHit = {
  readonly kind: "stock" | "etf";
  readonly symbol: string;
  readonly name: string;
  readonly market: "TW" | "US";
  readonly secondaryLabel: string | null;
};

export function twResultToSearchHit(row: TwSymbolSearchResult): SearchHit {
  return {
    kind: row.instrument_kind,
    symbol: row.symbol,
    name: row.name,
    market: "TW",
    secondaryLabel: row.industry_category ?? null,
  };
}

const US_ETF_QUOTE_TYPES = new Set(["ETF", "MUTUALFUND"]);
const US_STOCK_QUOTE_TYPES = new Set(["EQUITY"]);

/** `yfinance.Search` returns futures/currency/crypto/index quotes alongside equities/ETFs for a
 * broad query (e.g. "tesla" surfaces `STSLA=F` single-stock futures) — this palette is scoped to
 * 公司/ETF only, so anything outside those two `quote_type`s is dropped rather than mis-bucketed
 * into "stock" by default. */
export function usResultToSearchHit(row: UsSymbolSearchResult): SearchHit | null {
  const quoteType = row.quote_type ?? "";
  if (US_ETF_QUOTE_TYPES.has(quoteType)) {
    return {
      kind: "etf",
      symbol: row.symbol,
      name: row.long_name ?? row.short_name ?? row.symbol,
      market: "US",
      secondaryLabel: row.exchange,
    };
  }
  if (US_STOCK_QUOTE_TYPES.has(quoteType)) {
    return {
      kind: "stock",
      symbol: row.symbol,
      name: row.long_name ?? row.short_name ?? row.symbol,
      market: "US",
      secondaryLabel: row.exchange,
    };
  }
  return null;
}
