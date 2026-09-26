"use client";

import { useQuery } from "@tanstack/react-query";
import { getFundHoldings } from "@/lib/api/etf-detail";
import type { Market } from "@/types/settings";

/** Whether a US symbol is a fund (ETF/mutual fund) rather than a plain equity — the one real
 * signal available, since `classifySymbolType` always returns `"stock"` for US (TW's code-shape
 * ETF rule doesn't apply to US tickers; see `lib/symbol-type.ts`) and `get_company_profile` has
 * no quote-type field. `get_fund_holdings` resolving non-empty `holdings`/`sector_weightings`
 * means yfinance's own `Ticker.funds_data` returned something for this symbol — that only
 * happens for a real fund (`sources/yfinance/holders.py::_fund_holdings` degrades to empty for
 * any plain equity). `isLoading` lets the caller hold a neutral state until this resolves,
 * rather than flashing the stock tab bar and then swapping to the ETF one. */
export function useIsUsFund(
  symbol: string,
  market: Market,
): { isFund: boolean; isLoading: boolean } {
  const query = useQuery({
    queryKey: ["fund_holdings", symbol, market],
    queryFn: () => getFundHoldings(symbol, market),
    enabled: market === "US",
  });

  if (market !== "US") {
    return { isFund: false, isLoading: false };
  }
  const isFund = Boolean(
    query.data && (query.data.holdings.length > 0 || query.data.sector_weightings.length > 0),
  );
  return { isFund, isLoading: query.isLoading };
}
