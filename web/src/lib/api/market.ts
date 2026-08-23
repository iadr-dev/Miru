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
  type BusinessIndicatorResponse,
  businessIndicatorResponseSchema,
  type DayTradingResponse,
  type DerivativesResponse,
  dayTradingResponseSchema,
  derivativesResponseSchema,
  type EtfMoneyFlowResponse,
  etfMoneyFlowResponseSchema,
  type IndustryFlowResponse,
  type InstitutionalRankingResponse,
  industryFlowResponseSchema,
  institutionalRankingResponseSchema,
  type MacroIndicatorsResponse,
  type MarginMaintenanceResponse,
  macroIndicatorsResponseSchema,
  marginMaintenanceResponseSchema,
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
