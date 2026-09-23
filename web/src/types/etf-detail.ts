/**
 * ETF-detail-page contracts — mirrors `backend/src/api/routers/fund_data.py`'s
 * `/funds/{symbol}/{fund-info,expense-ratio,premium-discount}` routes plus
 * `market_rest.py`'s `/market/active-etf-report`. Split from `types/stock-detail.ts` the same
 * way that file was split from `types/market.ts` — a genuinely different per-symbol surface
 * (the ETF detail page), even though it shares the `healthStatusSchema`/`maskedTeaserSchema`
 * seams already defined there/in `types/market.ts` rather than redefining them.
 */
import { z } from "zod";
import { maskedTeaserSchema } from "@/types/market";
import { healthStatusSchema } from "@/types/stock-detail";

// ---------------------------------------------------------------------------
// GET /funds/{symbol}/fund-info — get_etf_fund_info (public)
// ---------------------------------------------------------------------------

export const etfFundInfoSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  fund_name: z.string(),
  fund_type: z.string(),
  tracked_index: z.string().nullable().optional(),
  units_outstanding: z.number().nullable().optional(),
  listed_date: z.string().nullable().optional(),
  inception_date: z.string().nullable().optional(),
  manager_name: z.string().nullable().optional(),
  manager_company: z.string().nullable().optional(),
  custodian_bank: z.string().nullable().optional(),
});
export type EtfFundInfo = z.infer<typeof etfFundInfoSchema>;

export const etfFundInfoResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  info: etfFundInfoSchema.nullable().optional(),
});
export type EtfFundInfoResponse = z.infer<typeof etfFundInfoResponseSchema>;

// ---------------------------------------------------------------------------
// GET /funds/{symbol}/expense-ratio — get_etf_expense_ratio (public)
// ---------------------------------------------------------------------------

export const etfExpenseInfoSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string().nullable().optional(),
  management_fee_pct: z.number().nullable().optional(),
  custody_fee_pct: z.number().nullable().optional(),
  total_expense_ratio_pct_approx: z.number().nullable().optional(),
  custodian_bank: z.string().nullable().optional(),
});
export type EtfExpenseInfo = z.infer<typeof etfExpenseInfoSchema>;

export const etfExpenseRatioResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  info: etfExpenseInfoSchema.nullable().optional(),
});
export type EtfExpenseRatioResponse = z.infer<typeof etfExpenseRatioResponseSchema>;

// ---------------------------------------------------------------------------
// GET /funds/{symbol}/premium-discount — get_etf_premium_discount (public)
// ---------------------------------------------------------------------------

export const etfNavRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string(),
  fund_name: z.string(),
  units_outstanding: z.number().nullable().optional(),
  units_outstanding_change: z.number().nullable().optional(),
  close_price: z.number().nullable().optional(),
  estimated_nav: z.number().nullable().optional(),
  premium_discount_pct: z.number().nullable().optional(),
  prior_day_nav: z.number().nullable().optional(),
});
export type EtfNavRow = z.infer<typeof etfNavRowSchema>;

export const etfPremiumDiscountResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  data_available: z.boolean().default(true),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(etfNavRowSchema).default([]),
  current_aum: z.number().nullable().optional(),
  aum_caveat: z.string().nullable().optional(),
});
export type EtfPremiumDiscountResponse = z.infer<typeof etfPremiumDiscountResponseSchema>;

// ---------------------------------------------------------------------------
// GET /market/active-etf-report — get_active_etf_report (sponsor)
// ---------------------------------------------------------------------------

export const HOLDING_STATUSES = ["新增", "刪除", "加碼", "減碼", "no_prior_data"] as const;
export const holdingStatusSchema = z.enum(HOLDING_STATUSES);
export type HoldingStatus = z.infer<typeof holdingStatusSchema>;

export const activeEtfReportRowSchema = z.object({
  component_stock_id: z.string(),
  component_stock_name: z.string(),
  status: holdingStatusSchema,
  shares_delta: z.number(),
  pct_change: z.number().nullable().optional(),
  current_weight: z.number().nullable().optional(),
  weight_delta_pct: z.number().nullable().optional(),
});
export type ActiveEtfReportRow = z.infer<typeof activeEtfReportRowSchema>;

export const activeEtfReportDataSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  symbol: z.string(),
  market: z.string(),
  report_date: z.string().nullable().optional(),
  prior_date: z.string().nullable().optional(),
  fund_size: z.number().nullable().optional(),
  fund_size_prior: z.number().nullable().optional(),
  fund_size_change_pct: z.number().nullable().optional(),
  premium_discount_pct: z.number().nullable().optional(),
  premium_discount_unavailable_reason: z.string().nullable().optional(),
  estimated_nav_per_unit: z.number().nullable().optional(),
  estimated_premium_discount_pct: z.number().nullable().optional(),
  estimation_caveat: z.string().nullable().optional(),
  rows: z.array(activeEtfReportRowSchema).default([]),
});
export type ActiveEtfReportData = z.infer<typeof activeEtfReportDataSchema>;

export const activeEtfReportResponseSchema = z.union([
  activeEtfReportDataSchema,
  maskedTeaserSchema,
]);
export type ActiveEtfReportResponse = z.infer<typeof activeEtfReportResponseSchema>;
