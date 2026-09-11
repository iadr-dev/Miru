/**
 * Stock-detail-page API client — mirrors `backend/src/api/routers/market_rest.py`'s `/stock/*`
 * routes + `/analytics/{symbol}/score` (`analytics.py`) exactly. Split from `lib/api/market.ts`
 * (whole-market shaped) into its own file for the same reason `types/stock-detail.ts` is split
 * from `types/market.ts` — a genuinely different, per-symbol surface. Reuses `market.ts`'s
 * `requestJson`/`MarketApiError` seam rather than duplicating it.
 */
import { MarketApiError, requestJson } from "@/lib/api/market";
import type { Market } from "@/types/settings";
import {
  type AnalystConsensusResponse,
  analystConsensusResponseSchema,
  type ChipFlowsResponse,
  type CompanyProfile,
  chipFlowsResponseSchema,
  companyProfileSchema,
  type DayTradingResponse,
  type DispositionSecuritiesResponse,
  type DividendResultResponse,
  dayTradingResponseSchema,
  dispositionSecuritiesResponseSchema,
  dividendResultResponseSchema,
  type EsgScoreResponse,
  esgScoreResponseSchema,
  type FinancialStatementResponse,
  type FinancialStatementType,
  type FinmindStockNewsResponse,
  type ForeignShareholdingResponse,
  financialStatementResponseSchema,
  finmindStockNewsResponseSchema,
  foreignShareholdingResponseSchema,
  type InsiderShareholdingResponse,
  type InstitutionalFlowResponse,
  insiderShareholdingResponseSchema,
  institutionalFlowResponseSchema,
  type MarginShortResponse,
  type MonthRevenueResponse,
  marginShortResponseSchema,
  monthRevenueResponseSchema,
  type PttStockSearchResponse,
  pttStockSearchResponseSchema,
  type SecuritiesLendingResponse,
  type StockCalendarEventsResponse,
  type StockInsiderTransferDeclarationResponse,
  type StockMaterialAnnouncementsResponse,
  type StockScoreResponse,
  type SymbolThemesResponse,
  securitiesLendingResponseSchema,
  stockCalendarEventsResponseSchema,
  stockInsiderTransferDeclarationResponseSchema,
  stockMaterialAnnouncementsResponseSchema,
  stockScoreResponseSchema,
  symbolThemesResponseSchema,
  type ValuationRatiosResponse,
  valuationRatiosResponseSchema,
} from "@/types/stock-detail";

export { MarketApiError };

type DateRangeParams = { startDate?: string; endDate?: string };

function symbolDateSearchParams(
  symbol: string,
  market: Market,
  params: DateRangeParams,
): URLSearchParams {
  const search = new URLSearchParams({ symbol, market });
  if (params.startDate) search.set("start_date", params.startDate);
  if (params.endDate) search.set("end_date", params.endDate);
  return search;
}

// ---------------------------------------------------------------------------
// 0. GET /market/material-announcements (public, per-symbol) — already wired before this
//    session's `/stock/*` batch; kept here since it's consumed by 基本資料's 公告/事件 stream.
// ---------------------------------------------------------------------------

export async function getStockMaterialAnnouncements(
  symbol: string,
  market: Market,
): Promise<StockMaterialAnnouncementsResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/market/material-announcements?${search.toString()}`);
  return stockMaterialAnnouncementsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 1. GET /stock/company-profile (public)
// ---------------------------------------------------------------------------

export async function getCompanyProfile(symbol: string, market: Market): Promise<CompanyProfile> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/company-profile?${search.toString()}`);
  return companyProfileSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 2. GET /analytics/{symbol}/score (public)
// ---------------------------------------------------------------------------

export async function getStockScore(symbol: string, market: Market): Promise<StockScoreResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/analytics/${symbol}/score?${search.toString()}`);
  return stockScoreResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 3. GET /stock/insider-shareholding (public)
// ---------------------------------------------------------------------------

export async function getInsiderShareholding(
  symbol: string,
  market: Market,
): Promise<InsiderShareholdingResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/insider-shareholding?${search.toString()}`);
  return insiderShareholdingResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 3b. GET /stock/insider-transfer-declarations (public) — 董監事持股 card's 持股轉讓 sub-tab
// ---------------------------------------------------------------------------

export async function getStockInsiderTransferDeclarations(
  symbol: string,
  market: Market,
  tradeDate?: string,
): Promise<StockInsiderTransferDeclarationResponse> {
  const search = new URLSearchParams({ symbol, market });
  if (tradeDate) search.set("trade_date", tradeDate);
  const raw = await requestJson(`/stock/insider-transfer-declarations?${search.toString()}`);
  return stockInsiderTransferDeclarationResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 4. GET /stock/esg-score (public, coverage-gated)
// ---------------------------------------------------------------------------

export async function getEsgScore(symbol: string, market: Market): Promise<EsgScoreResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/esg-score?${search.toString()}`);
  return esgScoreResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 5. GET /stock/disposition-securities (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getDispositionSecurities(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<DispositionSecuritiesResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/disposition-securities?${search.toString()}`);
  return dispositionSecuritiesResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 6. GET /stock/dividend-result (public)
// ---------------------------------------------------------------------------

export async function getDividendResult(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<DividendResultResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/dividend-result?${search.toString()}`);
  return dividendResultResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 7. GET /stock/month-revenue (public)
// ---------------------------------------------------------------------------

export async function getMonthRevenue(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<MonthRevenueResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/month-revenue?${search.toString()}`);
  return monthRevenueResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 8. GET /stock/financial-statement (public)
// ---------------------------------------------------------------------------

export async function getFinancialStatement(
  symbol: string,
  market: Market,
  statementType: FinancialStatementType,
  params: DateRangeParams = {},
): Promise<FinancialStatementResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  search.set("statement_type", statementType);
  const raw = await requestJson(`/stock/financial-statement?${search.toString()}`);
  return financialStatementResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 9. GET /stock/institutional-flow (public, per-symbol)
// ---------------------------------------------------------------------------

export async function getStockInstitutionalFlow(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<InstitutionalFlowResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/institutional-flow?${search.toString()}`);
  return institutionalFlowResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 10. GET /stock/margin-short (public, per-symbol)
// ---------------------------------------------------------------------------

export async function getStockMarginShort(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<MarginShortResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/margin-short?${search.toString()}`);
  return marginShortResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 11. GET /stock/securities-lending (public)
// ---------------------------------------------------------------------------

export async function getSecuritiesLending(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<SecuritiesLendingResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/securities-lending?${search.toString()}`);
  return securitiesLendingResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 12. GET /stock/chip-flows (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getStockChipFlows(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<ChipFlowsResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/chip-flows?${search.toString()}`);
  return chipFlowsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 13. GET /stock/valuation-ratios (public)
// ---------------------------------------------------------------------------

export async function getStockValuationRatios(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<ValuationRatiosResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/valuation-ratios?${search.toString()}`);
  return valuationRatiosResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 14. GET /stock/analyst-consensus (public, coverage-gated)
// ---------------------------------------------------------------------------

export async function getAnalystConsensus(
  symbol: string,
  market: Market,
): Promise<AnalystConsensusResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/analyst-consensus?${search.toString()}`);
  return analystConsensusResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 15. GET /stock/calendar-events (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getStockCalendarEvents(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<StockCalendarEventsResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/calendar-events?${search.toString()}`);
  return stockCalendarEventsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 16. GET /stock/finmind-news (public, per-symbol 財經新聞)
// ---------------------------------------------------------------------------

export async function getFinmindStockNews(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<FinmindStockNewsResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/finmind-news?${search.toString()}`);
  return finmindStockNewsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 17. GET /stock/ptt-search (public, best-effort per-symbol PTT body search)
// ---------------------------------------------------------------------------

export async function getPttStockSearch(
  symbol: string,
  companyName: string,
  lookbackDays = 7,
): Promise<PttStockSearchResponse> {
  const search = new URLSearchParams({
    symbol,
    company_name: companyName,
    lookback_days: String(lookbackDays),
  });
  const raw = await requestJson(`/stock/ptt-search?${search.toString()}`);
  return pttStockSearchResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 18. GET /symbols/{symbol}/themes (public) — 基本資料's 概念股 chip row
// ---------------------------------------------------------------------------

export async function getSymbolThemes(
  symbol: string,
  market: Market,
): Promise<SymbolThemesResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/symbols/${symbol}/themes?${search.toString()}`);
  return symbolThemesResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 19. GET /stock/foreign-shareholding (public) — 籌碼分析's 三大法人 column, 持股 sub-tab
// ---------------------------------------------------------------------------

export async function getForeignShareholding(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<ForeignShareholdingResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/foreign-shareholding?${search.toString()}`);
  return foreignShareholdingResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 20. GET /stock/day-trading (public) — 籌碼分析's 融資券 column, 當沖 sub-tab
// ---------------------------------------------------------------------------

export async function getStockDayTrading(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<DayTradingResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/day-trading?${search.toString()}`);
  return dayTradingResponseSchema.parse(raw);
}
