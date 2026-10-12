/**
 * Fundamentals — `/stock/month-revenue`, `/stock/financial-statement`,
 * `/stock/valuation-ratios`, `/stock/analyst-consensus`.
 */

import { z } from "zod";
import { healthStatusSchema } from "@/types/market/shared";

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
  // Defaults to "quarterly" — FinMind (TW's source) reports one frequency only; yfinance (US)
  // reports annual/quarterly/ttm as genuinely distinct frames (see backend
  // `FinancialStatementLine`'s own docstring).
  period_type: z.enum(["annual", "quarterly", "ttm"]).default("quarterly"),
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
  eps_trend_current: z.number().nullable().optional(),
  eps_trend_7_days_ago: z.number().nullable().optional(),
  eps_trend_30_days_ago: z.number().nullable().optional(),
  eps_trend_60_days_ago: z.number().nullable().optional(),
  eps_trend_90_days_ago: z.number().nullable().optional(),
  eps_revisions_up_last_30_days: z.number().nullable().optional(),
  eps_revisions_down_last_30_days: z.number().nullable().optional(),
  current_year_growth_estimate: z.number().nullable().optional(),
  next_year_growth_estimate: z.number().nullable().optional(),
});

export type AnalystConsensus = z.infer<typeof analystConsensusSchema>;

export const analystConsensusResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  consensus: analystConsensusSchema.nullable().optional(),
});

export type AnalystConsensusResponse = z.infer<typeof analystConsensusResponseSchema>;
