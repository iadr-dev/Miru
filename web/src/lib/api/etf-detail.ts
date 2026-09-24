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
  etfExpenseRatioResponseSchema,
  etfFundInfoResponseSchema,
  etfPremiumDiscountResponseSchema,
  etfThemeTagsResponseSchema,
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
// 5. GET /market/active-etf-report (Sponsor-tier)
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
