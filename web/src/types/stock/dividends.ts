/**
 * Dividends — `/stock/dividend-policy-summary`.
 */

import { z } from "zod";
import { healthStatusSchema } from "@/types/market/shared";

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
