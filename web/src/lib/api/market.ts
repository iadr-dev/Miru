/**
 * Market-dashboard API client — mirrors `backend/src/api/routers/market_rest.py` exactly
 * (that router's own docstring: 4 documented tools were never built and are deliberately absent,
 * so this client only ever calls routes that exist). Follows `lib/api/settings.ts`'s established
 * convention: `fetchWithRefresh` + `apiUrl` + Zod-parse-at-the-boundary + a typed error class.
 */
import { fetchWithRefresh } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/config";
import { apiErrorSchema } from "@/lib/api/schemas";
import {
  type AdvanceDeclineResponse,
  advanceDeclineResponseSchema,
  type BusinessIndicatorResponse,
  businessIndicatorResponseSchema,
  type DayTradingResponse,
  type DerivativesResponse,
  dayTradingResponseSchema,
  derivativesResponseSchema,
  type EtfMoneyFlowResponse,
  etfMoneyFlowResponseSchema,
  type HolderAccumulationResponse,
  type HolderThreshold,
  type HolderWindow,
  holderAccumulationResponseSchema,
  type IndexContributionResponse,
  type IndustryFlowResponse,
  type InstitutionalRankingResponse,
  indexContributionResponseSchema,
  industryFlowResponseSchema,
  institutionalRankingResponseSchema,
  type LeaderboardDirection,
  type LeaderboardUniverse,
  type MacroIndicatorsResponse,
  type MarginMaintenanceResponse,
  type MarginShortBalanceResponse,
  type MomentumPeriod,
  macroIndicatorsResponseSchema,
  marginMaintenanceResponseSchema,
  marginShortBalanceResponseSchema,
  type PriceHistoryResponse,
  type PriceMomentumResponse,
  priceHistoryResponseSchema,
  priceMomentumResponseSchema,
  type ScoreLeaderboardData,
  type SymbolInfoResponse,
  scoreLeaderboardDataSchema,
  symbolInfoResponseSchema,
} from "@/types/market";
import type { Market } from "@/types/settings";

export class MarketApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super(message);
    this.name = "MarketApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

async function requestJson(path: string): Promise<unknown> {
  const response = await fetchWithRefresh(apiUrl(path), {
    credentials: "include",
    headers: { accept: "application/json" },
  });
  if (!response.ok) {
    const parsed = apiErrorSchema.safeParse(await response.json().catch(() => null));
    if (parsed.success) {
      throw new MarketApiError(
        parsed.data.code,
        parsed.data.message,
        response.status,
        parsed.data.retryable,
      );
    }
    throw new MarketApiError(
      "unknown_error",
      `Request failed (${response.status})`,
      response.status,
      response.status >= 500,
    );
  }
  return response.json();
}

type DateRangeParams = { startDate?: string; endDate?: string };

function dateRangeSearchParams(params: DateRangeParams): URLSearchParams {
  const search = new URLSearchParams();
  if (params.startDate) search.set("start_date", params.startDate);
  if (params.endDate) search.set("end_date", params.endDate);
  return search;
}

// ---------------------------------------------------------------------------
// 1. GET /market/institutional-ranking
// ---------------------------------------------------------------------------

export async function getInstitutionalRanking(
  market: Market,
  params: DateRangeParams = {},
): Promise<InstitutionalRankingResponse> {
  const search = dateRangeSearchParams(params);
  search.set("market", market);
  const raw = await requestJson(`/market/institutional-ranking?${search.toString()}`);
  return institutionalRankingResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 2. GET /market/margin-maintenance (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getMarginMaintenance(
  market: Market,
  params: DateRangeParams = {},
): Promise<MarginMaintenanceResponse> {
  const search = dateRangeSearchParams(params);
  search.set("market", market);
  const raw = await requestJson(`/market/margin-maintenance?${search.toString()}`);
  return marginMaintenanceResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 3. GET /market/day-trading (per-symbol, public)
// ---------------------------------------------------------------------------

export async function getDayTrading(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<DayTradingResponse> {
  const search = dateRangeSearchParams(params);
  search.set("symbol", symbol);
  search.set("market", market);
  const raw = await requestJson(`/market/day-trading?${search.toString()}`);
  return dayTradingResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 4. GET /market/etf-money-flow (Sponsor-tier, bounded range required)
// ---------------------------------------------------------------------------

export async function getEtfMoneyFlow(
  market: Market,
  startDate: string,
  endDate: string,
  topN = 20,
): Promise<EtfMoneyFlowResponse> {
  const search = new URLSearchParams({
    market,
    start_date: startDate,
    end_date: endDate,
    top_n: String(topN),
  });
  const raw = await requestJson(`/market/etf-money-flow?${search.toString()}`);
  return etfMoneyFlowResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 5. GET /market/industry-flow (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getIndustryFlow(
  market: Market,
  params: DateRangeParams = {},
): Promise<IndustryFlowResponse> {
  const search = dateRangeSearchParams(params);
  search.set("market", market);
  const raw = await requestJson(`/market/industry-flow?${search.toString()}`);
  return industryFlowResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 6. GET /derivatives/{contract}
// ---------------------------------------------------------------------------

export async function getDerivatives(
  contract: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<DerivativesResponse> {
  const search = dateRangeSearchParams(params);
  search.set("market", market);
  const raw = await requestJson(
    `/derivatives/${encodeURIComponent(contract)}?${search.toString()}`,
  );
  return derivativesResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 7. GET /macro (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getMacroIndicators(
  params: DateRangeParams = {},
): Promise<MacroIndicatorsResponse> {
  const search = dateRangeSearchParams(params);
  const suffix = search.toString();
  const raw = await requestJson(suffix ? `/macro?${suffix}` : "/macro");
  return macroIndicatorsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 8. GET /macro/business-indicator (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getBusinessIndicator(
  market: Market,
  params: DateRangeParams = {},
): Promise<BusinessIndicatorResponse> {
  const search = dateRangeSearchParams(params);
  search.set("market", market);
  const raw = await requestJson(`/macro/business-indicator?${search.toString()}`);
  return businessIndicatorResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 9. GET /market/symbol-info + /market/price-history (public, batched ≤20 symbols)
// ---------------------------------------------------------------------------

const MAX_BATCH_SYMBOLS = 20;

function symbolsSearchParams(symbols: readonly string[], market: Market): URLSearchParams {
  if (symbols.length > MAX_BATCH_SYMBOLS) {
    throw new Error(`getSymbolInfo/getPriceHistory accept at most ${MAX_BATCH_SYMBOLS} symbols`);
  }
  const search = new URLSearchParams({ market });
  search.set("symbols", symbols.join(","));
  return search;
}

export async function getSymbolInfo(
  symbols: readonly string[],
  market: Market,
): Promise<SymbolInfoResponse> {
  const search = symbolsSearchParams(symbols, market);
  const raw = await requestJson(`/market/symbol-info?${search.toString()}`);
  return symbolInfoResponseSchema.parse(raw);
}

export async function getPriceHistory(
  symbols: readonly string[],
  market: Market,
  days: number,
): Promise<PriceHistoryResponse> {
  const search = symbolsSearchParams(symbols, market);
  search.set("days", String(days));
  const raw = await requestJson(`/market/price-history?${search.toString()}`);
  return priceHistoryResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 10. GET /analytics/rankings — get_score_leaderboard (public composite AI score)
// ---------------------------------------------------------------------------

export async function getScoreLeaderboard(
  market: Market,
  direction: LeaderboardDirection,
  profile: string,
  limit: number,
  universe: LeaderboardUniverse,
): Promise<ScoreLeaderboardData> {
  const search = new URLSearchParams({
    market,
    direction,
    profile,
    limit: String(limit),
    universe,
  });
  const raw = await requestJson(`/analytics/rankings?${search.toString()}`);
  return scoreLeaderboardDataSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 11. GET /market/price-momentum (public single-factor ranking)
// ---------------------------------------------------------------------------

export async function getPriceMomentum(
  market: Market,
  period: MomentumPeriod,
  asOf: string | undefined,
  topN: number,
): Promise<PriceMomentumResponse> {
  const search = new URLSearchParams({ market, period, top_n: String(topN) });
  if (asOf) search.set("as_of", asOf);
  const raw = await requestJson(`/market/price-momentum?${search.toString()}`);
  return priceMomentumResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 12. GET /market/holder-accumulation (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getHolderAccumulation(
  market: Market,
  threshold: HolderThreshold,
  window: HolderWindow,
  asOf: string | undefined,
  topN: number,
): Promise<HolderAccumulationResponse> {
  const search = new URLSearchParams({
    market,
    threshold: String(threshold),
    window: String(window),
    top_n: String(topN),
  });
  if (asOf) search.set("as_of", asOf);
  const raw = await requestJson(`/market/holder-accumulation?${search.toString()}`);
  return holderAccumulationResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 13. GET /market/advance-decline (public breadth histogram)
// ---------------------------------------------------------------------------

export async function getAdvanceDecline(
  market: Market,
  asOf?: string,
): Promise<AdvanceDeclineResponse> {
  const search = new URLSearchParams({ market });
  if (asOf) search.set("as_of", asOf);
  const raw = await requestJson(`/market/advance-decline?${search.toString()}`);
  return advanceDeclineResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 14. GET /market/index-contribution (Sponsor-tier, approximate estimate)
// ---------------------------------------------------------------------------

export async function getIndexContribution(
  market: Market,
  tradeDate?: string,
): Promise<IndexContributionResponse> {
  const search = new URLSearchParams({ market });
  if (tradeDate) search.set("trade_date", tradeDate);
  const raw = await requestJson(`/market/index-contribution?${search.toString()}`);
  return indexContributionResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 15. GET /market/margin-short-balance (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getMarginShortBalance(market: Market): Promise<MarginShortBalanceResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/market/margin-short-balance?${search.toString()}`);
  return marginShortBalanceResponseSchema.parse(raw);
}
