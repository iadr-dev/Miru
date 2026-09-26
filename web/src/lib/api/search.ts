/**
 * `GET /search` client — mirrors `backend/src/api/routers/context.py::symbol_search`. Follows
 * `lib/api/market.ts`'s established `requestJson` + Zod-parse-at-the-boundary convention.
 */
import { requestJson } from "@/lib/api/market";
import {
  type SearchHit,
  twResultToSearchHit,
  twSymbolSearchResponseSchema,
  usResultToSearchHit,
  usSymbolSearchResponseSchema,
} from "@/types/symbol-search";

/** `market` is deliberately narrower than the full `Market` union — this palette only searches
 * the two markets the detail page actually routes to today (TW roster scan / US yfinance live
 * search); GB/JP/EU are real `get_symbol_search` markets but have no UI entry point yet. */
export async function searchTwSymbols(query: string): Promise<readonly SearchHit[]> {
  const search = new URLSearchParams({ q: query, market: "TW" });
  const raw = await requestJson(`/search?${search.toString()}`);
  const parsed = twSymbolSearchResponseSchema.parse(raw);
  return parsed.rows.map(twResultToSearchHit);
}

export async function searchUsSymbols(query: string): Promise<readonly SearchHit[]> {
  const search = new URLSearchParams({ q: query, market: "US" });
  const raw = await requestJson(`/search?${search.toString()}`);
  const parsed = usSymbolSearchResponseSchema.parse(raw);
  return parsed.results.map(usResultToSearchHit).filter((hit): hit is SearchHit => hit !== null);
}
