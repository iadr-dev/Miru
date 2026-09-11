/**
 * Stock-detail-page contracts — the 14 `/stock/*` REST routes under
 * `backend/src/api/routers/market_rest.py` (base path `/api/v1`), plus `/analytics/{symbol}/score`
 * (`backend/src/api/routers/analytics.py`). Split from `types/market.ts` (which is whole-market
 * shaped) since this page is a genuinely different, per-symbol surface — kept in its own file
 * per web/CLAUDE.md §2's "feature-only types stay co-located" guidance, sized like a feature here
 * rather than dumped into the already-1200-line market file. Sponsor-tier row models are unioned
 * with `maskedTeaserSchema`; row field names mirror `backend/src/schemas/capabilities.py` exactly.
 */
import { z } from "zod";
import { maskedTeaserSchema, scoreResultSchema } from "@/types/market";

export const HEALTH_STATUSES = ["ok", "stale", "unreachable", "unavailable-tier"] as const;
export const healthStatusSchema = z.enum(HEALTH_STATUSES);

// ---------------------------------------------------------------------------
// 0. GET /market/material-announcements — get_material_announcements (public, per-symbol).
//    Already wired in market_rest.py before this session's `/stock/*` batch — kept here rather
//    than types/market.ts since it's consumed by the stock-detail page's 基本資料 tab.
// ---------------------------------------------------------------------------

export const materialAnnouncementSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  announced_date: z.string(),
  announced_time: z.string(),
  subject: z.string(),
  detail: z.string(),
  qualifying_clause: z.string().nullable().optional(),
});
export type MaterialAnnouncement = z.infer<typeof materialAnnouncementSchema>;

export const stockMaterialAnnouncementsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  announcements: z.array(materialAnnouncementSchema).default([]),
});
export type StockMaterialAnnouncementsResponse = z.infer<
  typeof stockMaterialAnnouncementsResponseSchema
>;

// ---------------------------------------------------------------------------
// 1. GET /stock/company-profile — get_company_profile (public)
// ---------------------------------------------------------------------------

export const dividendEventSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  announced_date: z.string(),
  fiscal_year: z.string(),
  cash_dividend: z.number().nullable().optional(),
  stock_dividend: z.number().nullable().optional(),
  cash_ex_dividend_date: z.string().nullable().optional(),
  stock_ex_dividend_date: z.string().nullable().optional(),
});
export type DividendEvent = z.infer<typeof dividendEventSchema>;

export const companyProfileSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  sector: z.string().nullable().optional(),
  industry: z.string().nullable().optional(),
  latest_dividend: dividendEventSchema.nullable().optional(),
  market_value: z.number().nullable().optional(),
  per: z.number().nullable().optional(),
  dividend_yield: z.number().nullable().optional(),
  eps: z.number().nullable().optional(),
  chairman: z.string().nullable().optional(),
  auditor: z.string().nullable().optional(),
  headquarters: z.string().nullable().optional(),
  website: z.string().nullable().optional(),
  capital: z.number().nullable().optional(),
  spokesperson: z.string().nullable().optional(),
  founding_date: z.string().nullable().optional(),
  industry_classification_code: z.string().nullable().optional(),
  revenue_yoy: z.number().nullable().optional(),
  margins: z.number().nullable().optional(),
  sector_caveat: z.string(),
  dividend_caveat: z.string(),
  missing_fields: z.array(z.string()).default([]),
});
export type CompanyProfile = z.infer<typeof companyProfileSchema>;

// ---------------------------------------------------------------------------
// 2. GET /analytics/{symbol}/score — get_stock_score (public)
// ---------------------------------------------------------------------------

export const stockScoreResponseSchema = z.object({
  found: z.boolean(),
  symbol: z.string(),
  market: z.string(),
  profile: z.string(),
  result: scoreResultSchema.nullable().optional(),
  message: z.string().default(""),
});
export type StockScoreResponse = z.infer<typeof stockScoreResponseSchema>;

// ---------------------------------------------------------------------------
// 3. GET /stock/insider-shareholding — get_insider_shareholding (public)
// ---------------------------------------------------------------------------

export const directorShareholdingSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string(),
  person_name: z.string(),
  title: z.string(),
  shares_at_election: z.number().nullable().optional(),
  current_shares: z.number().nullable().optional(),
  pledged_shares: z.number().nullable().optional(),
  pledged_ratio: z.string().nullable().optional(),
});
export type DirectorShareholding = z.infer<typeof directorShareholdingSchema>;

export const insiderShareholdingResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  holdings: z.array(directorShareholdingSchema).default([]),
});
export type InsiderShareholdingResponse = z.infer<typeof insiderShareholdingResponseSchema>;

// ---------------------------------------------------------------------------
// 3b. GET /stock/insider-transfer-declarations — get_insider_transfer_declarations
//     (public), filtered server-side to one symbol. Mirrors
//     `InsiderTransferDeclarationRow` (backend/src/schemas/capabilities.py) field-for-field.
//     Whole-market feed, ingest-owned Parquet store — `data_available: false` when ingest has
//     no partition yet for the requested day, distinct from "available but zero rows for this
//     symbol" (data_available stays true, rows is just empty).
// ---------------------------------------------------------------------------

export const insiderTransferDeclarationRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  filer_role: z.string(),
  person_name: z.string(),
  transfer_method: z.string(),
  transfer_shares: z.number(),
  max_daily_exchange_shares: z.number().nullable().optional(),
  transferee: z.string().nullable().optional(),
  current_shares_own: z.number(),
  current_shares_trust: z.number(),
  planned_shares_own: z.number(),
  planned_shares_trust: z.number(),
  projected_shares_own_after: z.number(),
  projected_shares_trust_after: z.number(),
  transfer_window_start: z.string(),
  transfer_window_end: z.string(),
});
export type InsiderTransferDeclarationRow = z.infer<typeof insiderTransferDeclarationRowSchema>;

export const stockInsiderTransferDeclarationResponseSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable().optional(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  available_dates: z.array(z.string()).default([]),
  rows: z.array(insiderTransferDeclarationRowSchema).default([]),
});
export type StockInsiderTransferDeclarationResponse = z.infer<
  typeof stockInsiderTransferDeclarationResponseSchema
>;

// ---------------------------------------------------------------------------
// 4. GET /stock/esg-score — get_esg_score (public, coverage-gated)
// ---------------------------------------------------------------------------

export const esgScoreSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string(),
  total_esg_score: z.number().nullable().optional(),
  esg_risk_percentile: z.number().nullable().optional(),
});
export type EsgScoreValue = z.infer<typeof esgScoreSchema>;

export const esgScoreResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  esg: esgScoreSchema.nullable().optional(),
});
export type EsgScoreResponse = z.infer<typeof esgScoreResponseSchema>;

// ---------------------------------------------------------------------------
// 5. GET /stock/disposition-securities — get_disposition_securities (Sponsor-tier)
// ---------------------------------------------------------------------------

export const dispositionSecuritiesRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  date: z.string(),
  stock_name: z.string(),
  disposition_cnt: z.number(),
  condition: z.string(),
  measure: z.string(),
});
export type DispositionSecuritiesRow = z.infer<typeof dispositionSecuritiesRowSchema>;

export const dispositionSecuritiesDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(dispositionSecuritiesRowSchema).default([]),
});
export type DispositionSecuritiesData = z.infer<typeof dispositionSecuritiesDataSchema>;
export const dispositionSecuritiesResponseSchema = z.union([
  dispositionSecuritiesDataSchema,
  maskedTeaserSchema,
]);
export type DispositionSecuritiesResponse = z.infer<typeof dispositionSecuritiesResponseSchema>;

// ---------------------------------------------------------------------------
// 6. GET /stock/dividend-result — get_dividend_result (public)
// ---------------------------------------------------------------------------

export const dividendResultRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  before_price: z.number(),
  after_price: z.number(),
  stock_and_cache_dividend: z.number(),
  stock_or_cache_dividend: z.string(),
  max_price: z.number(),
  min_price: z.number(),
  open_price: z.number(),
  reference_price: z.number(),
});
export type DividendResultRow = z.infer<typeof dividendResultRowSchema>;

export const dividendResultResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(dividendResultRowSchema).default([]),
});
export type DividendResultResponse = z.infer<typeof dividendResultResponseSchema>;

// ---------------------------------------------------------------------------
// 7. GET /stock/month-revenue — get_month_revenue (public)
// ---------------------------------------------------------------------------

export const monthRevenueRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  country: z.string(),
  revenue: z.number(),
  revenue_month: z.number(),
  revenue_year: z.number(),
  create_time: z.string().nullable().optional(),
});
export type MonthRevenueRow = z.infer<typeof monthRevenueRowSchema>;

export const monthRevenueTrendPointSchema = z.object({
  revenue_year: z.number(),
  revenue_month: z.number(),
  revenue: z.number(),
  mom_pct: z.number().nullable().optional(),
  yoy_pct: z.number().nullable().optional(),
});
export type MonthRevenueTrendPoint = z.infer<typeof monthRevenueTrendPointSchema>;

export const monthRevenueResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  trend: z.array(monthRevenueTrendPointSchema).default([]),
  rows: z.array(monthRevenueRowSchema).default([]),
});
export type MonthRevenueResponse = z.infer<typeof monthRevenueResponseSchema>;

// ---------------------------------------------------------------------------
// 8. GET /stock/financial-statement — get_financial_statement (public)
// ---------------------------------------------------------------------------

export const FINANCIAL_STATEMENT_TYPES = [
  "income_statement",
  "balance_sheet",
  "cash_flow",
] as const;
export const financialStatementTypeSchema = z.enum(FINANCIAL_STATEMENT_TYPES);
export type FinancialStatementType = z.infer<typeof financialStatementTypeSchema>;

export const financialStatementLineSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  period_end: z.string(),
  statement_type: z.string(),
  item: z.string(),
  origin_name: z.string(),
  value: z.number(),
});
export type FinancialStatementLine = z.infer<typeof financialStatementLineSchema>;

export const financialStatementResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  lines: z.array(financialStatementLineSchema).default([]),
});
export type FinancialStatementResponse = z.infer<typeof financialStatementResponseSchema>;

// ---------------------------------------------------------------------------
// 9. GET /stock/institutional-flow — get_institutional_flow (public, per-symbol)
// ---------------------------------------------------------------------------

export const institutionalFlowRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  investor: z.string(),
  buy: z.number(),
  sell: z.number(),
});
export type InstitutionalFlowRow = z.infer<typeof institutionalFlowRowSchema>;

export const institutionalFlowRollingNetSchema = z.object({
  investor: z.string(),
  net_5d: z.number().nullable().optional(),
  net_10d: z.number().nullable().optional(),
  net_20d: z.number().nullable().optional(),
});
export type InstitutionalFlowRollingNet = z.infer<typeof institutionalFlowRollingNetSchema>;

export const institutionalFlowResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rolling_net: z.array(institutionalFlowRollingNetSchema).default([]),
  rows: z.array(institutionalFlowRowSchema).default([]),
});
export type InstitutionalFlowResponse = z.infer<typeof institutionalFlowResponseSchema>;

// ---------------------------------------------------------------------------
// 10. GET /stock/margin-short — get_margin_short (public, per-symbol)
// ---------------------------------------------------------------------------

export const marginShortRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  margin_purchase_buy: z.number(),
  margin_purchase_cash_repayment: z.number(),
  margin_purchase_limit: z.number(),
  margin_purchase_sell: z.number(),
  margin_purchase_today_balance: z.number(),
  margin_purchase_yesterday_balance: z.number(),
  note: z.string().nullable().optional(),
  offset_loan_and_short: z.number(),
  short_sale_buy: z.number(),
  short_sale_cash_repayment: z.number(),
  short_sale_limit: z.number(),
  short_sale_sell: z.number(),
  short_sale_today_balance: z.number(),
  short_sale_yesterday_balance: z.number(),
});
export type MarginShortRow = z.infer<typeof marginShortRowSchema>;

export const marginShortResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(marginShortRowSchema).default([]),
});
export type MarginShortResponse = z.infer<typeof marginShortResponseSchema>;

// ---------------------------------------------------------------------------
// 11. GET /stock/securities-lending — get_securities_lending (public)
// ---------------------------------------------------------------------------

export const securitiesLendingRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  transaction_type: z.string(),
  volume: z.number(),
  fee_rate: z.number(),
  close: z.number(),
  original_return_date: z.string().nullable().optional(),
});
export type SecuritiesLendingRow = z.infer<typeof securitiesLendingRowSchema>;

export const securitiesLendingResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(securitiesLendingRowSchema).default([]),
});
export type SecuritiesLendingResponse = z.infer<typeof securitiesLendingResponseSchema>;

// ---------------------------------------------------------------------------
// 12. GET /stock/chip-flows — get_chip_flows (Sponsor-tier)
// ---------------------------------------------------------------------------

export const chipBrokerRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  broker_id: z.string(),
  broker_name: z.string(),
  price: z.number(),
  buy: z.number(),
  sell: z.number(),
});
export type ChipBrokerRow = z.infer<typeof chipBrokerRowSchema>;

export const chipBrokerNetSummarySchema = z.object({
  broker_id: z.string(),
  broker_name: z.string(),
  net: z.number(),
});
export type ChipBrokerNetSummary = z.infer<typeof chipBrokerNetSummarySchema>;

export const chipFlowsDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  top_brokers: z.array(chipBrokerNetSummarySchema).default([]),
  rows: z.array(chipBrokerRowSchema).default([]),
});
export type ChipFlowsData = z.infer<typeof chipFlowsDataSchema>;
export const chipFlowsResponseSchema = z.union([chipFlowsDataSchema, maskedTeaserSchema]);
export type ChipFlowsResponse = z.infer<typeof chipFlowsResponseSchema>;

// ---------------------------------------------------------------------------
// 13. GET /stock/valuation-ratios — get_valuation_ratios (public)
// ---------------------------------------------------------------------------

export const valuationRatiosRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  dividend_yield: z.number(),
  per: z.number(),
  pbr: z.number(),
});
export type ValuationRatiosRow = z.infer<typeof valuationRatiosRowSchema>;

export const valuationBandSummarySchema = z.object({
  window_start: z.string(),
  window_end: z.string(),
  per_min: z.number(),
  per_max: z.number(),
  per_current: z.number(),
  pbr_min: z.number(),
  pbr_max: z.number(),
  pbr_current: z.number(),
  dividend_yield_min: z.number(),
  dividend_yield_max: z.number(),
  dividend_yield_current: z.number(),
});
export type ValuationBandSummary = z.infer<typeof valuationBandSummarySchema>;

export const valuationRatiosResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  band: valuationBandSummarySchema.nullable().optional(),
  rows: z.array(valuationRatiosRowSchema).default([]),
});
export type ValuationRatiosResponse = z.infer<typeof valuationRatiosResponseSchema>;

// ---------------------------------------------------------------------------
// 14. GET /stock/analyst-consensus — get_analyst_consensus (public, coverage-gated)
// ---------------------------------------------------------------------------

export const analystConsensusSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string(),
  mean_target: z.number().nullable().optional(),
  median_target: z.number().nullable().optional(),
  high_target: z.number().nullable().optional(),
  low_target: z.number().nullable().optional(),
  number_of_analysts: z.number().default(0),
  recommendation_key: z.string().nullable().optional(),
  strong_buy: z.number().nullable().optional(),
  buy: z.number().nullable().optional(),
  hold: z.number().nullable().optional(),
  sell: z.number().nullable().optional(),
  strong_sell: z.number().nullable().optional(),
  latest_upgrade_downgrade_firm: z.string().nullable().optional(),
  latest_upgrade_downgrade_action: z.string().nullable().optional(),
  forward_eps_estimate: z.number().nullable().optional(),
  forward_revenue_estimate: z.number().nullable().optional(),
  long_term_growth_estimate: z.number().nullable().optional(),
});
export type AnalystConsensus = z.infer<typeof analystConsensusSchema>;

export const analystConsensusResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  consensus: analystConsensusSchema.nullable().optional(),
});
export type AnalystConsensusResponse = z.infer<typeof analystConsensusResponseSchema>;

// ---------------------------------------------------------------------------
// 15. GET /stock/calendar-events — get_calendar_events (Sponsor-tier)
// ---------------------------------------------------------------------------

export const calendarEventOutSchema = z.object({
  id: z.string(),
  stock_id: z.string().nullable().optional(),
  type: z.string(),
  title_zh: z.string(),
  title_en: z.string(),
  event_date: z.string().nullable().optional(),
  window_start: z.string().nullable().optional(),
  window_end: z.string().nullable().optional(),
  state: z.string(),
  source: z.string(),
});
export type CalendarEventOut = z.infer<typeof calendarEventOutSchema>;

export const stockCalendarEventsDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  events: z.array(calendarEventOutSchema).default([]),
});
export type StockCalendarEventsData = z.infer<typeof stockCalendarEventsDataSchema>;
export const stockCalendarEventsResponseSchema = z.union([
  stockCalendarEventsDataSchema,
  maskedTeaserSchema,
]);
export type StockCalendarEventsResponse = z.infer<typeof stockCalendarEventsResponseSchema>;

// ---------------------------------------------------------------------------
// 16. GET /stock/finmind-news — get_finmind_stock_news (public, per-symbol 財經新聞)
// ---------------------------------------------------------------------------

export const stockNewsRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  description: z.string(),
  link: z.string(),
  source: z.string(),
  title: z.string(),
});
export type StockNewsRow = z.infer<typeof stockNewsRowSchema>;

export const finmindStockNewsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(stockNewsRowSchema).default([]),
});
export type FinmindStockNewsResponse = z.infer<typeof finmindStockNewsResponseSchema>;

// ---------------------------------------------------------------------------
// 17. GET /stock/ptt-search — get_ptt_stock_search (public). Best-effort per-symbol PTT
//     search — matches symbol id/company name against each candidate post's BODY text (never
//     just the title), across Stock/Tech_Job/Warrant boards, over a caller-chosen lookback
//     window. `posted_date` mirrors `PttPostOut`/`sources/ptt.py::PttPost` field-for-field.
// ---------------------------------------------------------------------------

export const pttStockSearchPostSchema = z.object({
  board: z.string(),
  board_tag: z.string(),
  title: z.string(),
  post_tag: z.string().nullable().optional(),
  author: z.string(),
  push_count: z.string(),
  link: z.string(),
  posted_date: z.string().nullable().optional(),
});
export type PttStockSearchPost = z.infer<typeof pttStockSearchPostSchema>;

export const pttStockSearchResponseSchema = z.object({
  currentness_caveat: z.string(),
  posts: z.array(pttStockSearchPostSchema).default([]),
});
export type PttStockSearchResponse = z.infer<typeof pttStockSearchResponseSchema>;

// ---------------------------------------------------------------------------
// 18. GET /symbols/{symbol}/themes — get_symbol_themes (public). Already wired in
//     backend/src/api/routers/themes.py before this session — kept here since it's consumed by
//     the stock-detail page's 基本資料 tab (概念股 chips).
// ---------------------------------------------------------------------------

export const symbolThemeSchema = z.object({
  theme_key: z.string(),
  name_zh: z.string(),
  kind: z.string(),
  sub_industry: z.string().default(""),
  confidence: z.number().default(1),
});
export type SymbolTheme = z.infer<typeof symbolThemeSchema>;

export const symbolThemesResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  theme_count: z.number().default(0),
  themes: z.array(symbolThemeSchema).default([]),
});
export type SymbolThemesResponse = z.infer<typeof symbolThemesResponseSchema>;

// ---------------------------------------------------------------------------
// 19. GET /stock/foreign-shareholding — get_foreign_shareholding (public). Mirrors
//     `ForeignShareholdingRow` (backend/src/schemas/capabilities.py) field-for-field.
//     籌碼分析's 三大法人 column, 持股 sub-tab — daily 外資持股比 (foreign-ownership ratio).
// ---------------------------------------------------------------------------

export const foreignShareholdingRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  stock_name: z.string(),
  international_code: z.string(),
  foreign_investment_remaining_shares: z.number(),
  foreign_investment_shares: z.number(),
  foreign_investment_remain_ratio: z.number(),
  foreign_investment_shares_ratio: z.number(),
  foreign_investment_upper_limit_ratio: z.number(),
  chinese_investment_upper_limit_ratio: z.number(),
  number_of_shares_issued: z.number(),
  recently_declare_date: z.string().nullable().optional(),
  note: z.string().nullable().optional(),
});
export type ForeignShareholdingRow = z.infer<typeof foreignShareholdingRowSchema>;

export const foreignShareholdingResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(foreignShareholdingRowSchema).default([]),
});
export type ForeignShareholdingResponse = z.infer<typeof foreignShareholdingResponseSchema>;

// ---------------------------------------------------------------------------
// 20. GET /stock/day-trading — get_day_trading (public). Mirrors `DayTradingRow`
//     (backend/src/schemas/capabilities.py) field-for-field. 籌碼分析's 融資券 column, 當沖
//     sub-tab.
// ---------------------------------------------------------------------------

export const dayTradingRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  buy_after_sale: z.string(),
  volume: z.number(),
  buy_amount: z.number(),
  sell_amount: z.number(),
});
export type DayTradingRow = z.infer<typeof dayTradingRowSchema>;

export const dayTradingResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(dayTradingRowSchema).default([]),
});
export type DayTradingResponse = z.infer<typeof dayTradingResponseSchema>;
