/**
 * Stock-detail-page API client — mirrors `backend/src/api/routers/market_rest.py`'s `/stock/*`
 * routes + `/analytics/{symbol}/score` (`analytics.py`) exactly. Split from `lib/api/market.ts`
 * (whole-market shaped) into its own file for the same reason `types/stock-detail.ts` is split
 * from `types/market.ts` — a genuinely different, per-symbol surface. Reuses `market.ts`'s
 * `requestJson`/`MarketApiError` seam rather than duplicating it.
 */
import { type DateRangeParams, requestJson } from "@/lib/api/market";
import { type LivePriceQuoteResponse, livePriceQuoteResponseSchema } from "@/types/live-quote";
import type { Market } from "@/types/settings";
import {
  type StockScoreResponse,
  type StockSummaryResponse,
  stockScoreResponseSchema,
  stockSummaryResponseSchema,
  type TrendScoreResponse,
  trendScoreResponseSchema,
} from "@/types/stock/ai";
import {
  type ChainPositionsResponse,
  chainPositionsResponseSchema,
} from "@/types/stock/chain-position";
import {
  type ChipFlowsResponse,
  chipFlowsResponseSchema,
  type DayTradingResponse,
  dayTradingResponseSchema,
  type GovernmentBankFlowsResponse,
  governmentBankFlowsResponseSchema,
  type InstitutionalFlowResponse,
  institutionalFlowResponseSchema,
  type MarginShortResponse,
  marginShortResponseSchema,
  type SecuritiesLendingResponse,
  securitiesLendingResponseSchema,
} from "@/types/stock/chip";
import {
  type DividendPolicySummaryResponse,
  dividendPolicySummaryResponseSchema,
} from "@/types/stock/dividends";
import {
  type CalendarEventsFeedResponse,
  calendarEventsFeedResponseSchema,
  type StockCalendarEventsResponse,
  stockCalendarEventsResponseSchema,
} from "@/types/stock/events";
import {
  type AnalystConsensusResponse,
  analystConsensusResponseSchema,
  type FinancialStatementResponse,
  type FinancialStatementType,
  financialStatementResponseSchema,
  type MonthRevenueResponse,
  monthRevenueResponseSchema,
  type ValuationRatiosResponse,
  valuationRatiosResponseSchema,
} from "@/types/stock/fundamentals";
import {
  type FinmindStockNewsResponse,
  finmindStockNewsResponseSchema,
  type PttStockSearchResponse,
  pttStockSearchResponseSchema,
  type SecFilingsResponse,
  secFilingsResponseSchema,
} from "@/types/stock/news";
import {
  type EtfsHoldingStockResponse,
  etfsHoldingStockResponseSchema,
  type ForeignShareholdingResponse,
  foreignShareholdingResponseSchema,
  type InsiderShareholdingResponse,
  insiderShareholdingResponseSchema,
  type ShareholderDispersionResponse,
  type StockInsiderTransferDeclarationResponse,
  shareholderDispersionResponseSchema,
  stockInsiderTransferDeclarationResponseSchema,
} from "@/types/stock/ownership";
import {
  type CompanyProfile,
  companyProfileSchema,
  type EsgScoreResponse,
  esgScoreResponseSchema,
  type StockMaterialAnnouncementsResponse,
  type StockPeersResponse,
  type SupplyChainEdgesResponse,
  type SymbolThemesResponse,
  stockMaterialAnnouncementsResponseSchema,
  stockPeersResponseSchema,
  supplyChainEdgesResponseSchema,
  symbolThemesResponseSchema,
  type ThemeTierHeatmapResponse,
  themeTierHeatmapResponseSchema,
} from "@/types/stock/profile";
import {
  type IntradayKbarResponse,
  intradayKbarResponseSchema,
  type LiveQuoteResponse,
  liveQuoteResponseSchema,
  type PriceLimitResponse,
  type PriceTickResponse,
  priceLimitResponseSchema,
  priceTickResponseSchema,
} from "@/types/stock/realtime";
import {
  type TechnicalIndicatorsResponse,
  technicalIndicatorsResponseSchema,
} from "@/types/stock/technical";

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

export async function getStockScore(
  symbol: string,
  market: Market,
  profile: "swing" | "position" = "swing",
): Promise<StockScoreResponse> {
  const search = new URLSearchParams({ market, profile });
  const raw = await requestJson(`/analytics/${symbol}/score?${search.toString()}`);
  return stockScoreResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 2b. GET /analytics/{symbol}/trend-score (public) — AI tab's 6-dimension 趨勢評分 card
// ---------------------------------------------------------------------------

export async function getTrendScore(
  symbol: string,
  market: Market,
  days = 90,
): Promise<TrendScoreResponse> {
  const search = new URLSearchParams({ market, days: String(days) });
  const raw = await requestJson(`/analytics/${symbol}/trend-score?${search.toString()}`);
  return trendScoreResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 2c. GET /analytics/{symbol}/summary (Sponsor-tier) — AI tab's AI-generated SWOT summary card
// ---------------------------------------------------------------------------

export async function getStockSummary(
  symbol: string,
  market: Market,
): Promise<StockSummaryResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/analytics/${symbol}/summary?${search.toString()}`);
  return stockSummaryResponseSchema.parse(raw);
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
// 6c. GET /stock/dividend-policy-summary (public)
// ---------------------------------------------------------------------------

export async function getDividendPolicySummary(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<DividendPolicySummaryResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/dividend-policy-summary?${search.toString()}`);
  return dividendPolicySummaryResponseSchema.parse(raw);
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
// 12b. GET /stock/government-bank-flows (Sponsor-tier)
// ---------------------------------------------------------------------------

export async function getGovernmentBankFlows(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<GovernmentBankFlowsResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/government-bank-flows?${search.toString()}`);
  return governmentBankFlowsResponseSchema.parse(raw);
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
// 14a. GET /stock/sec-filings (public, US-only)
// ---------------------------------------------------------------------------

export async function getSecFilings(symbol: string, market: Market): Promise<SecFilingsResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/sec-filings?${search.toString()}`);
  return secFilingsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 14b. GET /stock/peers (public)
// ---------------------------------------------------------------------------

export async function getStockPeers(symbol: string, market: Market): Promise<StockPeersResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/peers?${search.toString()}`);
  return stockPeersResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 14c. GET /stock/live-price-quote (public) — AssetHeader's one-shot US price fallback
// ---------------------------------------------------------------------------

export async function getLivePriceQuote(
  symbol: string,
  market: Market,
): Promise<LivePriceQuoteResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/live-price-quote?${search.toString()}`);
  return livePriceQuoteResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 14d. GET /stock/calendar-events-feed (public) — US 總覽's upcoming-events calendar card
// ---------------------------------------------------------------------------

export async function getCalendarEventsFeed(market: Market): Promise<CalendarEventsFeedResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/stock/calendar-events-feed?${search.toString()}`);
  return calendarEventsFeedResponseSchema.parse(raw);
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

// ---------------------------------------------------------------------------
// 21. GET /stock/technical-indicators (public) — 技術分析's K線 chart data source
// ---------------------------------------------------------------------------

export async function getTechnicalIndicators(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<TechnicalIndicatorsResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/technical-indicators?${search.toString()}`);
  return technicalIndicatorsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 22. GET /stock/shareholder-dispersion (Sponsor-tier) — 技術分析's 散戶 副圖 overlay
// ---------------------------------------------------------------------------

export async function getShareholderDispersion(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<ShareholderDispersionResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/shareholder-dispersion?${search.toString()}`);
  return shareholderDispersionResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// 23. GET /stock/etfs-holding-stock (Sponsor-tier) — 追蹤 tab's ETF持有 card
// ---------------------------------------------------------------------------

export async function getEtfsHoldingStock(
  symbol: string,
  market: Market,
  asOf?: string,
): Promise<EtfsHoldingStockResponse> {
  const search = new URLSearchParams({ symbol, market });
  if (asOf) search.set("as_of", asOf);
  const raw = await requestJson(`/stock/etfs-holding-stock?${search.toString()}`);
  return etfsHoldingStockResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// GET /stock/live-quote (public) — 即時 tab's 五檔 ladder
// ---------------------------------------------------------------------------

export async function getLiveQuote(symbol: string, market: Market): Promise<LiveQuoteResponse> {
  const search = new URLSearchParams({ symbol, market });
  const raw = await requestJson(`/stock/live-quote?${search.toString()}`);
  return liveQuoteResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// GET /stock/price-limit (public) — 看盤 tab's 詳細 stats row
// ---------------------------------------------------------------------------

export async function getPriceLimit(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<PriceLimitResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/price-limit?${search.toString()}`);
  return priceLimitResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// GET /stock/intraday-kbar (Sponsor-tier) — 即時 tab's 分K chart
// ---------------------------------------------------------------------------

export async function getIntradayKbar(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<IntradayKbarResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/intraday-kbar?${search.toString()}`);
  return intradayKbarResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// GET /stock/price-tick (Sponsor-tier) — 即時 tab's 逐筆 list (EOD replay, not live)
// ---------------------------------------------------------------------------

export async function getPriceTick(
  symbol: string,
  market: Market,
  params: DateRangeParams = {},
): Promise<PriceTickResponse> {
  const search = symbolDateSearchParams(symbol, market, params);
  const raw = await requestJson(`/stock/price-tick?${search.toString()}`);
  return priceTickResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// GET /symbols/{symbol}/supply-chain (public) — 產業分析's 供應鏈關係圖 card
// ---------------------------------------------------------------------------

export async function getSupplyChainEdges(
  symbol: string,
  market: Market,
): Promise<SupplyChainEdgesResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/symbols/${symbol}/supply-chain?${search.toString()}`);
  return supplyChainEdgesResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// GET /symbols/{symbol}/chain-positions (public) — 產業分析's 上/中/下游 position badge
// ---------------------------------------------------------------------------

export async function getChainPositions(
  symbol: string,
  market: Market,
): Promise<ChainPositionsResponse> {
  const search = new URLSearchParams({ market });
  const raw = await requestJson(`/symbols/${symbol}/chain-positions?${search.toString()}`);
  return chainPositionsResponseSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// GET /themes/{key}/tier-heatmap (public) — 產業分析's 產業鏈 tier-tile view
// ---------------------------------------------------------------------------

export async function getThemeTierHeatmap(themeKey: string): Promise<ThemeTierHeatmapResponse> {
  const raw = await requestJson(`/themes/${encodeURIComponent(themeKey)}/tier-heatmap`);
  return themeTierHeatmapResponseSchema.parse(raw);
}
