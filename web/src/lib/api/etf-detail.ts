/**
 * ETF-detail-page API client — mirrors `backend/src/api/routers/fund_data.py`'s
 * `/funds/{symbol}/*` routes plus `market_rest.py`'s `/market/active-etf-report`. Split from
 * `lib/api/stock-detail.ts` for the same reason that file is split from `lib/api/market.ts` — a
 * genuinely different per-symbol surface (the ETF detail page), reusing `market.ts`'s
 * `requestJson`/`MarketApiError` seam rather than duplicating it.
 */
import { requestJson } from "@/lib/api/market";
import {
  type ActiveEtfReportResponse,
  activeEtfReportResponseSchema,
  type EtfExpenseRatioResponse,
  type EtfFundInfoResponse,
  type EtfPremiumDiscountResponse,
  type EtfThemeTagsResponse,
  type EtfTrailingReturnsResponse,
  etfExpenseRatioResponseSchema,
  etfFundInfoResponseSchema,
  etfPremiumDiscountResponseSchema,
  etfThemeTagsResponseSchema,
  etfTrailingReturnsResponseSchema,
  type FundComparisonResponse,
  type FundHoldingsResponse,
  type FundProfileResponse,
  fundComparisonResponseSchema,
  fundHoldingsResponseSchema,
  fundProfileResponseSchema,
} from "@/types/etf-detail";
import type { Market } from "@/types/settings";

// ---------------------------------------------------------------------------
// 1. GET /funds/{symbol}/fund-info (public)
// ---------------------------------------------------------------------------

export async function getEtfFundInfo(symbol: string, market: Market): Promise<EtfFundInfoResponse> {
  const raw = await requestJson(`/funds/${encodeURIComponent(symbol)}/fund-info?market=${market}`);
  return etfFundInfoResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 2. GET /funds/{symbol}/expense-ratio (public)
// ---------------------------------------------------------------------------

export async function getEtfExpenseRatio(
  symbol: string,
  market: Market,
): Promise<EtfExpenseRatioResponse> {
  const raw = await requestJson(
    `/funds/${encodeURIComponent(symbol)}/expense-ratio?market=${market}`,
  );
  return etfExpenseRatioResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 3. GET /funds/{symbol}/theme-tags (public)
// ---------------------------------------------------------------------------

export async function getEtfThemeTags(
  symbol: string,
  market: Market,
): Promise<EtfThemeTagsResponse> {
  const raw = await requestJson(`/funds/${encodeURIComponent(symbol)}/theme-tags?market=${market}`);
  return etfThemeTagsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 4. GET /funds/{symbol}/premium-discount (public)
// ---------------------------------------------------------------------------

export async function getEtfPremiumDiscount(
  symbol: string,
  market: Market,
  days = 60,
): Promise<EtfPremiumDiscountResponse> {
  const search = new URLSearchParams({ market, days: String(days) });
  const raw = await requestJson(
    `/funds/${encodeURIComponent(symbol)}/premium-discount?${search.toString()}`,
  );
  return etfPremiumDiscountResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 5. GET /funds/{symbol}/trailing-returns (public)
// ---------------------------------------------------------------------------

export async function getEtfTrailingReturns(
  symbol: string,
  market: Market,
): Promise<EtfTrailingReturnsResponse> {
  const raw = await requestJson(
    `/funds/${encodeURIComponent(symbol)}/trailing-returns?market=${market}`,
  );
  return etfTrailingReturnsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 6. GET /funds/comparison (public) — single-symbol call only (the ETF 總覽 tab's own
//    "since inception" stat); the multi-symbol chat-card comparison path has its own
//    generic-card rendering and doesn't go through this typed client.
// ---------------------------------------------------------------------------

export async function getFundTotalReturn(
  symbol: string,
  market: Market,
  lookbackDays: number,
): Promise<FundComparisonResponse> {
  const search = new URLSearchParams({
    symbols: `${symbol}:${market}`,
    lookback_days: String(lookbackDays),
  });
  const raw = await requestJson(`/funds/comparison?${search.toString()}`);
  return fundComparisonResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 7. GET /market/active-etf-report (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getActiveEtfReport(
  symbol: string,
  market: Market,
  tradeDate?: string,
): Promise<ActiveEtfReportResponse> {
  const search = new URLSearchParams({ symbol, market });
  if (tradeDate) search.set("trade_date", tradeDate);
  const raw = await requestJson(`/market/active-etf-report?${search.toString()}`);
  return activeEtfReportResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 8. GET /funds/{symbol}/holdings (public, non-TW) — top holdings + sector weightings from
//    yfinance's own FundsData; also the client-side signal for "is this US symbol a fund"
//    (see the schema's own comment in types/etf-detail.ts).
// ---------------------------------------------------------------------------

export async function getFundHoldings(
  symbol: string,
  market: Market,
): Promise<FundHoldingsResponse> {
  const raw = await requestJson(`/funds/${encodeURIComponent(symbol)}/holdings?market=${market}`);
  return fundHoldingsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 9. GET /funds/{symbol}/profile (public, non-TW) — AUM/category/issuer/YTD-return/
//    distribution-yield/inception-date, the 總覽 tab's fund-equivalent of company-profile.
// ---------------------------------------------------------------------------

export async function getFundProfile(symbol: string, market: Market): Promise<FundProfileResponse> {
  const raw = await requestJson(`/funds/${encodeURIComponent(symbol)}/profile?market=${market}`);
  return fundProfileResponseSchema.parse(raw);
}
