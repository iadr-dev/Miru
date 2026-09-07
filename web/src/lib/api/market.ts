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
  type DailySignalResponse,
  type DayTradingResponse,
  type DerivativesResponse,
  type DispositionBoardResponse,
  type DividendCalendarResponse,
  dailySignalResponseSchema,
  dayTradingResponseSchema,
  derivativesResponseSchema,
  dispositionBoardResponseSchema,
  dividendCalendarResponseSchema,
  type EtfListResponse,
  type EtfMoneyFlowResponse,
  etfListResponseSchema,
  etfMoneyFlowResponseSchema,
  type FuturesSnapshotResponse,
  futuresSnapshotResponseSchema,
  type HolderAccumulationResponse,
  type HolderThreshold,
  type HolderWindow,
  holderAccumulationResponseSchema,
  type IndexContributionResponse,
  type IndustryFlowResponse,
  type InsiderTransferDeclarationResponse,
  type InstitutionalRankingResponse,
  indexContributionResponseSchema,
  industryFlowResponseSchema,
  insiderTransferDeclarationResponseSchema,
  institutionalRankingResponseSchema,
  type LeaderboardDirection,
  type LeaderboardUniverse,
  type MacroIndicatorsResponse,
  type MarginMaintenanceResponse,
  type MarginShortBalanceResponse,
  type MarketIndexStripResponse,
  type MaterialAnnouncementFeedResponse,
  type MomentumPeriod,
  macroIndicatorsResponseSchema,
  marginMaintenanceResponseSchema,
  marginShortBalanceResponseSchema,
  marketIndexStripResponseSchema,
  materialAnnouncementFeedResponseSchema,
  type OptionVixResponse,
  optionVixResponseSchema,
  type PriceHistoryResponse,
  type PriceMomentumResponse,
  priceHistoryResponseSchema,
  priceMomentumResponseSchema,
  type ScoreLeaderboardData,
  type SymbolInfoResponse,
  scoreLeaderboardDataSchema,
  symbolInfoResponseSchema,
  type TickSnapshotResponse,
  tickSnapshotResponseSchema,
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

// ---------------------------------------------------------------------------
// 16. GET /market/etf-list (public whole-market ETF list)
// ---------------------------------------------------------------------------

export async function getEtfList(market: Market, tradeDate?: string): Promise<EtfListResponse> {
  const search = new URLSearchParams({ market });
  if (tradeDate) search.set("trade_date", tradeDate);
  const raw = await requestJson(`/market/etf-list?${search.toString()}`);
  return etfListResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 17. GET /market/daily-signal (Sponsor-tier, 今日訊號)
// ---------------------------------------------------------------------------

export async function getDailySignal(
  market: Market,
  tradeDate?: string,
): Promise<DailySignalResponse> {
  const search = new URLSearchParams({ market });
  if (tradeDate) search.set("trade_date", tradeDate);
  const raw = await requestJson(`/market/daily-signal?${search.toString()}`);
  return dailySignalResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 18. GET /market/dividend-calendar (public, ETF-only 股息日曆)
// ---------------------------------------------------------------------------

export async function getDividendCalendar(
  market: Market,
  year?: number,
  month?: number,
): Promise<DividendCalendarResponse> {
  const search = new URLSearchParams({ market });
  if (year !== undefined) search.set("year", String(year));
  if (month !== undefined) search.set("month", String(month));
  const raw = await requestJson(`/market/dividend-calendar?${search.toString()}`);
  return dividendCalendarResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 21. GET /market/index-strip (public, TW-tab ticker strip: 加權指數/櫃買指數/美元/黃金/原油)
// ---------------------------------------------------------------------------

export async function getMarketIndexStrip(): Promise<MarketIndexStripResponse> {
  const raw = await requestJson("/market/index-strip");
  return marketIndexStripResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 22. GET /market/tick-snapshot (Sponsor-tier, one stock/ETF/index-code snapshot)
// ---------------------------------------------------------------------------

export async function getTickSnapshot(
  symbol: string,
  market: Market = "TW",
): Promise<TickSnapshotResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/market/tick-snapshot?${search.toString()}`);
  return tickSnapshotResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 23. GET /market/futures-snapshot (Sponsor-tier, one futures-contract snapshot)
// ---------------------------------------------------------------------------

export async function getFuturesSnapshot(
  symbol: string,
  market: Market = "TW",
): Promise<FuturesSnapshotResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/market/futures-snapshot?${search.toString()}`);
  return futuresSnapshotResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 24. GET /derivatives/taiex-vix (Sponsor-tier, 臺指選擇權波動率指數)
// ---------------------------------------------------------------------------

export async function getTaiexVix(params: DateRangeParams = {}): Promise<OptionVixResponse> {
  const search = dateRangeSearchParams(params);
  const raw = await requestJson(`/derivatives/taiex-vix?${search.toString()}`);
  return optionVixResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 19. GET /market/material-announcement-feed (public, whole-market 重大訊息 feed)
// ---------------------------------------------------------------------------

export async function getMaterialAnnouncementFeed(
  market: Market,
  tradeDate?: string,
  category?: string,
): Promise<MaterialAnnouncementFeedResponse> {
  const search = new URLSearchParams({ market });
  if (tradeDate) search.set("trade_date", tradeDate);
  if (category) search.set("category", category);
  const raw = await requestJson(`/market/material-announcement-feed?${search.toString()}`);
  return materialAnnouncementFeedResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 20. GET /market/insider-transfer-declarations (public, whole-market 內部人股權轉讓事前申報
//     feed, t187ap12_L only)
// ---------------------------------------------------------------------------

export async function getInsiderTransferDeclarations(
  market: Market,
  tradeDate?: string,
): Promise<InsiderTransferDeclarationResponse> {
  const search = new URLSearchParams({ market });
  if (tradeDate) search.set("trade_date", tradeDate);
  const raw = await requestJson(`/market/insider-transfer-declarations?${search.toString()}`);
  return insiderTransferDeclarationResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 25. GET /market/disposition-board (Sponsor-tier, 處置王-style board)
// ---------------------------------------------------------------------------

export async function getDispositionBoard(
  market: Market = "TW",
): Promise<DispositionBoardResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/market/disposition-board?${search.toString()}`);
  return dispositionBoardResponseSchema.parse(raw);
}
