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
// 2b. GET /analytics/{symbol}/trend-score — get_trend_score (public). AI tab's 6-dimension
//     趨勢評分 card — a `dimensions[].value` of `null` reports its own `unavailable_reason`
//     rather than a fabricated score; `history[].close` is the dual-axis chart's price overlay.
// ---------------------------------------------------------------------------

export const trendScoreDimensionSchema = z.object({
  name: z.string(),
  value: z.number().min(0).max(100).nullable(),
  description: z.string(),
  unavailable_reason: z.string().nullable(),
});
export type TrendScoreDimension = z.infer<typeof trendScoreDimensionSchema>;

export const trendScoreHistoryPointSchema = z.object({
  trade_date: z.string(),
  composite: z.number().min(0).max(100).nullable(),
  close: z.number().nullable(),
});
export type TrendScoreHistoryPoint = z.infer<typeof trendScoreHistoryPointSchema>;

export const trendScoreDataSchema = z.object({
  found: z.boolean(),
  symbol: z.string(),
  market: z.string(),
  as_of: z.string().nullable(),
  composite: z.number().min(0).max(100).nullable(),
  dimensions: z.array(trendScoreDimensionSchema),
  unavailable_dimensions: z.array(z.string()),
  history: z.array(trendScoreHistoryPointSchema),
  non_trading_caveat: z.string(),
  message: z.string(),
});
export type TrendScoreData = z.infer<typeof trendScoreDataSchema>;
// Public — never Sponsor-masked, matching `priceLimitResponseSchema`'s own un-unioned shape.
export const trendScoreResponseSchema = trendScoreDataSchema;
export type TrendScoreResponse = z.infer<typeof trendScoreResponseSchema>;

// ---------------------------------------------------------------------------
// 2c. GET /analytics/{symbol}/summary — get_stock_summary (Sponsor-tier). AI tab's AI-generated
//     headline/narrative + SWOT summary card.
// ---------------------------------------------------------------------------

export const stockSwotSchema = z.object({
  strengths: z.array(z.string()),
  weaknesses: z.array(z.string()),
  opportunities: z.array(z.string()),
  threats: z.array(z.string()),
});
export type StockSwot = z.infer<typeof stockSwotSchema>;

export const stockSummaryDataSchema = z.object({
  found: z.boolean(),
  symbol: z.string(),
  market: z.string(),
  as_of: z.string().nullable(),
  headline: z.string(),
  narrative: z.string(),
  swot: stockSwotSchema,
  non_trading_caveat: z.string(),
  message: z.string(),
});
export type StockSummaryData = z.infer<typeof stockSummaryDataSchema>;
export const stockSummaryResponseSchema = z.union([stockSummaryDataSchema, maskedTeaserSchema]);
export type StockSummaryResponse = z.infer<typeof stockSummaryResponseSchema>;

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
// 6b. GET /stock/dividend-policy-summary — get_dividend_policy_summary (public)
// ---------------------------------------------------------------------------

export const dividendYearSummarySchema = z.object({
  year: z.number(),
  cash_dividend: z.number(),
  stock_dividend: z.number(),
  total_dividend: z.number(),
});
export type DividendYearSummary = z.infer<typeof dividendYearSummarySchema>;

export const dividendFillResultSchema = z.object({
  trade_date: z.string(),
  before_price: z.number(),
  filled: z.boolean().nullable(),
  filled_date: z.string().nullable().optional(),
});
export type DividendFillResult = z.infer<typeof dividendFillResultSchema>;

export const dividendPolicyStatsSchema = z.object({
  latest_year: z.number().nullable().optional(),
  latest_year_total_dividend: z.number().nullable().optional(),
  cash_dividend_yield_pct: z.number().nullable().optional(),
  payout_ratio_pct: z.number().nullable().optional(),
  fill_rate_pct: z.number().nullable().optional(),
  fill_rate_sample_size: z.number().default(0),
});
export type DividendPolicyStats = z.infer<typeof dividendPolicyStatsSchema>;

export const dividendPolicySummaryResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  events: z.array(dividendEventSchema).default([]),
  year_summaries: z.array(dividendYearSummarySchema).default([]),
  fill_results: z.array(dividendFillResultSchema).default([]),
  stats: dividendPolicyStatsSchema,
});
export type DividendPolicySummaryResponse = z.infer<typeof dividendPolicySummaryResponseSchema>;

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
  cumulative_yoy_pct: z.number().nullable().optional(),
  is_period_high: z.boolean().default(false),
  period_end_close: z.number().nullable().optional(),
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

export const chipBrokerTopRowSchema = z.object({
  rank: z.number(),
  broker_id: z.string().nullable(),
  broker_name: z.string().nullable(),
  net: z.number().nullable(),
  buy: z.number().nullable(),
  sell: z.number().nullable(),
  locked: z.boolean().default(false),
});
export type ChipBrokerTopRow = z.infer<typeof chipBrokerTopRowSchema>;

export const chipFlowsDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  total_volume: z.number().default(0),
  main_force_net: z.number().default(0),
  concentration_days_pct: z.number().nullable().optional(),
  latest_trade_date: z.string().nullable().optional(),
  free_row_count: z.number().default(0),
  buy_top: z.array(chipBrokerTopRowSchema).default([]),
  sell_top: z.array(chipBrokerTopRowSchema).default([]),
  top_brokers: z.array(chipBrokerNetSummarySchema).default([]),
  rows: z.array(chipBrokerRowSchema).default([]),
});
export type ChipFlowsData = z.infer<typeof chipFlowsDataSchema>;
export const chipFlowsResponseSchema = z.union([chipFlowsDataSchema, maskedTeaserSchema]);
export type ChipFlowsResponse = z.infer<typeof chipFlowsResponseSchema>;

// ---------------------------------------------------------------------------
// 12b. GET /stock/government-bank-flows — get_government_bank_flows (Sponsor-tier). 籌碼分析's
//      摘要 sub-tab's 4th 法人籤碼 category (八大行庫), same masked-teaser union `chip-flows`
//      above already uses.
// ---------------------------------------------------------------------------

export const governmentBankFlowRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  buy_amount: z.number(),
  sell_amount: z.number(),
  buy: z.number(),
  sell: z.number(),
  bank_name: z.string(),
});
export type GovernmentBankFlowRow = z.infer<typeof governmentBankFlowRowSchema>;

export const governmentBankFlowsDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(governmentBankFlowRowSchema).default([]),
});
export type GovernmentBankFlowsData = z.infer<typeof governmentBankFlowsDataSchema>;
export const governmentBankFlowsResponseSchema = z.union([
  governmentBankFlowsDataSchema,
  maskedTeaserSchema,
]);
export type GovernmentBankFlowsResponse = z.infer<typeof governmentBankFlowsResponseSchema>;

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

// ---------------------------------------------------------------------------
// 21. GET /stock/technical-indicators — get_technical_indicators (public). Mirrors
//     `GetTechnicalIndicatorsOutput`/`analytics/models.py`'s indicator-series models field-for-
//     field. 技術分析's K線 chart overlay/subpane data source.
// ---------------------------------------------------------------------------

export const smaResultSchema = z.object({
  window: z.number(),
  values: z.array(z.number().nullable()).default([]),
});

export const movingAverageBundleSchema = z.object({
  ma5: smaResultSchema,
  ma10: smaResultSchema,
  ma20: smaResultSchema,
  ma60: smaResultSchema,
  ma120: smaResultSchema,
  ma240: smaResultSchema,
});
export type MovingAverageBundle = z.infer<typeof movingAverageBundleSchema>;

export const bollingerResultSchema = z.object({
  window: z.number(),
  mid: z.array(z.number().nullable()).default([]),
  upper: z.array(z.number().nullable()).default([]),
  lower: z.array(z.number().nullable()).default([]),
});
export type BollingerResult = z.infer<typeof bollingerResultSchema>;

export const kdResultSchema = z.object({
  period: z.number(),
  k: z.array(z.number().nullable()).default([]),
  d: z.array(z.number().nullable()).default([]),
});
export type KdResult = z.infer<typeof kdResultSchema>;

export const macdResultSchema = z.object({
  dif: z.array(z.number().nullable()).default([]),
  dem: z.array(z.number().nullable()).default([]),
  histogram: z.array(z.number().nullable()).default([]),
});
export type MacdResult = z.infer<typeof macdResultSchema>;

export const supportResistanceLevelSchema = z.object({
  kind: z.string(),
  price: z.number(),
  bar_index: z.number(),
});
export type SupportResistanceLevel = z.infer<typeof supportResistanceLevelSchema>;

export const supportResistanceResultSchema = z.object({
  levels: z.array(supportResistanceLevelSchema).default([]),
});
export type SupportResistanceResult = z.infer<typeof supportResistanceResultSchema>;

export const candlestickPatternHitSchema = z.object({
  bar_index: z.number(),
  pattern: z.string(),
  direction: z.number(),
});
export type CandlestickPatternHit = z.infer<typeof candlestickPatternHitSchema>;

export const eodBarSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number(),
  turnover: z.number().nullable().optional(),
});
export type EodBar = z.infer<typeof eodBarSchema>;

export const technicalIndicatorsResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  data_available: z.boolean().default(true),
  unavailable_reason: z.string().nullable().optional(),
  bars: z.array(eodBarSchema).default([]),
  trade_dates: z.array(z.string()).default([]),
  moving_averages: movingAverageBundleSchema.nullable().optional(),
  bollinger: bollingerResultSchema.nullable().optional(),
  kd: kdResultSchema.nullable().optional(),
  macd: macdResultSchema.nullable().optional(),
  support_resistance: supportResistanceResultSchema.nullable().optional(),
  candlestick_patterns: z.array(candlestickPatternHitSchema).default([]),
  non_trading_caveat: z.string(),
});
export type TechnicalIndicatorsResponse = z.infer<typeof technicalIndicatorsResponseSchema>;

// ---------------------------------------------------------------------------
// 22. GET /stock/shareholder-dispersion — get_shareholder_dispersion (Sponsor-tier). 技術分析/
//     籌碼分析's 散戶 副圖 overlay — 股權持股分級表 rows.
// ---------------------------------------------------------------------------

export const shareholdingDistributionRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  holding_shares_level: z.string(),
  people: z.number(),
  percent: z.number(),
  unit: z.number(),
});
export type ShareholdingDistributionRow = z.infer<typeof shareholdingDistributionRowSchema>;

export const shareholderDispersionDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(shareholdingDistributionRowSchema).default([]),
});
export const shareholderDispersionResponseSchema = z.union([
  shareholderDispersionDataSchema,
  maskedTeaserSchema,
]);
export type ShareholderDispersionResponse = z.infer<typeof shareholderDispersionResponseSchema>;

// ---------------------------------------------------------------------------
// GET /stock/etfs-holding-stock — get_etfs_holding_stock (Sponsor-tier). Mirrors
// `GetEtfsHoldingStockOutput`/`market_tools.py` field-for-field. 追蹤 tab's ETF持有 card
// (active-management ETFs only).
// ---------------------------------------------------------------------------

export const etfHoldingRankRowSchema = z.object({
  rank: z.number(),
  etf_symbol: z.string().nullable(),
  etf_name: z.string().nullable(),
  shares: z.number().nullable(),
  shares_delta: z.number().nullable().optional(),
  stock_capital_pct: z.number().nullable().optional(),
  stock_capital_pct_delta: z.number().nullable().optional(),
  locked: z.boolean().default(false),
});
export type EtfHoldingRankRow = z.infer<typeof etfHoldingRankRowSchema>;

export const etfWeightRankRowSchema = z.object({
  rank: z.number(),
  etf_symbol: z.string().nullable(),
  etf_name: z.string().nullable(),
  weight_pct: z.number().nullable(),
  weight_pct_delta: z.number().nullable().optional(),
  quarter_min_weight_pct: z.number().nullable().optional(),
  quarter_max_weight_pct: z.number().nullable().optional(),
  locked: z.boolean().default(false),
});
export type EtfWeightRankRow = z.infer<typeof etfWeightRankRowSchema>;

export const etfPositionEventSchema = z.object({
  event_date: z.string(),
  etf_symbol: z.string().nullable(),
  etf_name: z.string().nullable(),
  event: z.enum(["新增", "刪除", "加碼", "減碼", "no_prior_data"]),
  shares_delta: z.number().nullable(),
  locked: z.boolean().default(false),
});
export type EtfPositionEvent = z.infer<typeof etfPositionEventSchema>;

export const etfPositionEventCountsSchema = z.object({
  new_count: z.number().default(0),
  removed_count: z.number().default(0),
  increased_count: z.number().default(0),
  decreased_count: z.number().default(0),
});
export type EtfPositionEventCounts = z.infer<typeof etfPositionEventCountsSchema>;

export const etfsHoldingStockDataSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string().nullable().optional(),
  prior_date: z.string().nullable().optional(),
  data_available: z.boolean().default(true),
  unavailable_reason: z.string().nullable().optional(),
  etf_count: z.number().default(0),
  free_row_count: z.number().default(0),
  holding_rank: z.array(etfHoldingRankRowSchema).default([]),
  weight_rank: z.array(etfWeightRankRowSchema).default([]),
  position_event_counts: etfPositionEventCountsSchema.default({
    new_count: 0,
    removed_count: 0,
    increased_count: 0,
    decreased_count: 0,
  }),
  position_events: z.array(etfPositionEventSchema).default([]),
});
export const etfsHoldingStockResponseSchema = z.union([
  etfsHoldingStockDataSchema,
  maskedTeaserSchema,
]);
export type EtfsHoldingStockResponse = z.infer<typeof etfsHoldingStockResponseSchema>;

// ---------------------------------------------------------------------------
// GET /stock/live-quote — get_live_quote_snapshot (public — TWSE MIS, never Sponsor-gated).
// Mirrors `LiveQuoteSnapshot`/`GetLiveQuoteSnapshotOutput` (backend/src/tools/live_tools.py)
// field-for-field. 即時 tab's 五檔 ladder. `best_bid`/`best_ask`/`best_bid_volume`/
// `best_ask_volume` are `_`-delimited 5-tier strings (see that module's own docstring) — split
// client-side, never re-parsed on the backend into a typed array (the raw upstream shape).
// ---------------------------------------------------------------------------

export const liveQuoteSnapshotSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  name: z.string().nullable().optional(),
  last_price: z.number(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  volume: z.number(),
  best_bid: z.string().nullable().optional(),
  best_ask: z.string().nullable().optional(),
  best_bid_volume: z.string().nullable().optional(),
  best_ask_volume: z.string().nullable().optional(),
  observed_at: z.string().nullable().optional(),
});
export type LiveQuoteSnapshot = z.infer<typeof liveQuoteSnapshotSchema>;

export const liveQuoteResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  snapshot: liveQuoteSnapshotSchema.nullable(),
});
export type LiveQuoteResponse = z.infer<typeof liveQuoteResponseSchema>;

// ---------------------------------------------------------------------------
// GET /stock/price-limit — get_price_limit (public). Mirrors `PriceLimitRow`
// (backend/src/schemas/capabilities.py) field-for-field. 看盤 tab's 詳細 stats row — daily
// 參考/漲停/跌停 price. A `limit_up`/`limit_down` of 0 means no price-change limit applies
// (leveraged/inverse ETFs, emerging-board stocks), not missing data.
// ---------------------------------------------------------------------------

export const priceLimitRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  reference_price: z.number(),
  limit_up: z.number(),
  limit_down: z.number(),
});
export type PriceLimitRow = z.infer<typeof priceLimitRowSchema>;

export const priceLimitResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(priceLimitRowSchema).default([]),
});
export type PriceLimitResponse = z.infer<typeof priceLimitResponseSchema>;

// ---------------------------------------------------------------------------
// GET /stock/intraday-kbar — get_intraday_kbar (Sponsor-tier). Mirrors `KBarRow`
// (backend/src/schemas/capabilities.py) field-for-field. 即時 tab's 分K chart — one trading
// day of minute bars, published ~15:50 the same day (not a live feed during market hours).
// ---------------------------------------------------------------------------

export const intradayKbarRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  minute: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number(),
});
export type IntradayKbarRow = z.infer<typeof intradayKbarRowSchema>;

export const intradayKbarDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(intradayKbarRowSchema).default([]),
});
export const intradayKbarResponseSchema = z.union([intradayKbarDataSchema, maskedTeaserSchema]);
export type IntradayKbarResponse = z.infer<typeof intradayKbarResponseSchema>;

// ---------------------------------------------------------------------------
// GET /stock/price-tick — get_price_tick (Sponsor-tier). Mirrors `PriceTickRow`
// (backend/src/schemas/capabilities.py) field-for-field. 即時 tab's 逐筆 list — one trading
// day's tick-by-tick trades, published ~15:30 the same day: an END-OF-DAY REPLAY, never a
// live-ticking stream — render copy must say so, not imply real-time.
// ---------------------------------------------------------------------------

export const priceTickRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  time: z.string(),
  deal_price: z.number(),
  volume: z.number(),
  tick_type: z.string(),
});
export type PriceTickRow = z.infer<typeof priceTickRowSchema>;

export const priceTickDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  rows: z.array(priceTickRowSchema).default([]),
});
export const priceTickResponseSchema = z.union([priceTickDataSchema, maskedTeaserSchema]);
export type PriceTickResponse = z.infer<typeof priceTickResponseSchema>;

// ---------------------------------------------------------------------------
// GET /symbols/{symbol}/supply-chain — get_supply_chain_edges (public). Mirrors
// `SupplyChainEdge`/`GetSupplyChainEdgesOutput` (backend/src/tools/theme_tools.py) field-for-
// field. 產業分析 tab's 供應鏈關係圖 card — every supply/customer/competitor edge involving one
// symbol, from either side; `from_symbol`/`to_symbol` disambiguate direction per edge.
// ---------------------------------------------------------------------------

export const EDGE_TYPES = ["supplies", "customer", "competes"] as const;
export const edgeTypeSchema = z.enum(EDGE_TYPES);
export type EdgeType = z.infer<typeof edgeTypeSchema>;

export const EDGE_CONFIDENCES = ["high", "medium", "low"] as const;
export const edgeConfidenceSchema = z.enum(EDGE_CONFIDENCES);
export type EdgeConfidence = z.infer<typeof edgeConfidenceSchema>;

export const supplyChainEdgeSchema = z.object({
  from_symbol: z.string(),
  to_symbol: z.string(),
  market: z.string(),
  edge_type: edgeTypeSchema,
  confidence: edgeConfidenceSchema,
  revenue_pct: z.number().nullable().optional(),
  source: z.enum(["seed", "llm_extraction"]),
  from_name: z.string(),
  to_name: z.string(),
});
export type SupplyChainEdge = z.infer<typeof supplyChainEdgeSchema>;

export const supplyChainEdgesResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  edge_count: z.number().default(0),
  edges: z.array(supplyChainEdgeSchema).default([]),
});
export type SupplyChainEdgesResponse = z.infer<typeof supplyChainEdgesResponseSchema>;

// ---------------------------------------------------------------------------
// GET /themes/{key}/tier-heatmap — get_theme_tier_heatmap (public). Mirrors
// `SubIndustryHeat`/`GetThemeTierHeatmapOutput` (backend/src/tools/theme_tools.py) field-for-
// field. 產業分析 tab's 產業鏈 tier-tile view — a theme's upstream/midstream/downstream sub-
// industry tiles, each colored by that sub-industry's own members' real average price change.
// A `null` `pct_change` means no member had a real close on both days (never a fabricated 0%).
// ---------------------------------------------------------------------------

export const subIndustryHeatSchema = z.object({
  sub_industry: z.string(),
  tier: z.enum(["upstream", "midstream", "downstream"]),
  tier_source: z.enum(["curated", "heuristic"]),
  pct_change: z.number().nullable().optional(),
  member_count: z.number().default(0),
  priced_member_count: z.number().default(0),
});
export type SubIndustryHeat = z.infer<typeof subIndustryHeatSchema>;

export const themeTierHeatmapResponseSchema = z.object({
  theme_key: z.string(),
  found: z.boolean(),
  trade_date: z.string().nullable().optional(),
  prior_trade_date: z.string().nullable().optional(),
  data_available: z.boolean().default(true),
  unavailable_reason: z.string().nullable().optional(),
  upstream: z.array(subIndustryHeatSchema).default([]),
  midstream: z.array(subIndustryHeatSchema).default([]),
  downstream: z.array(subIndustryHeatSchema).default([]),
  message: z.string().default(""),
});
export type ThemeTierHeatmapResponse = z.infer<typeof themeTierHeatmapResponseSchema>;

// ---------------------------------------------------------------------------
// GET /themes/{key} — get_theme_members (public). Mirrors `ThemeMember`/
// `GetThemeMembersOutput` (backend/src/tools/theme_tools.py) field-for-field. 產業分析 tab's
// 產業鏈 tier-tile click-through — the member symbols of one sub-industry, filtered client-side
// from the theme's full member list (this route has no server-side sub_industry filter).
// ---------------------------------------------------------------------------

export const themeMemberSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  sub_industry: z.string().default(""),
  confidence: z.number().default(1),
  source: z.string(),
});
export type ThemeMember = z.infer<typeof themeMemberSchema>;

export const themeMembersResponseSchema = z.object({
  theme_key: z.string(),
  found: z.boolean(),
  name_zh: z.string().default(""),
  name_en: z.string().default(""),
  kind: z.string().nullable().optional(),
  member_count: z.number().default(0),
  members: z.array(themeMemberSchema).default([]),
  message: z.string().default(""),
});
export type ThemeMembersResponse = z.infer<typeof themeMembersResponseSchema>;
