"use client";

import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { searchTwSymbols, searchUsSymbols } from "@/lib/api/search";
import type { SearchHit } from "@/types/symbol-search";

/** One query per market — TW (ingested-roster scan) and US (live yfinance search) are genuinely
 * distinct calls with distinct latency/rate-limit profiles, so a failure in one never blanks the
 * other's results (web/CLAUDE.md §6: "one failing view never blanks the page"). Query key mirrors
 * the backend's own `(capability, market, query)` shape. Relies on `query-provider.tsx`'s default
 * `staleTime`/`gcTime`/`retry` — type-ahead results are cheap, short-lived reads, not a surface
 * with its own freshness class. */
export function useSymbolSearch(
  query: string,
  market: "TW" | "US",
): UseQueryResult<readonly SearchHit[]> {
  return useQuery({
    queryKey: ["symbol_search", market, query],
    queryFn: () => (market === "TW" ? searchTwSymbols(query) : searchUsSymbols(query)),
    enabled: query.length > 0,
  });
}
