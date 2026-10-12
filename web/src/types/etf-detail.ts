/**
 * ETF-detail-page contracts — mirrors `backend/src/api/routers/fund_data.py`'s
 * `/funds/{symbol}/{fund-info,expense-ratio,premium-discount}` routes plus
 * `market_rest.py`'s `/market/active-etf-report` — the ETF detail page's own per-symbol
 * surface. Shared seams (`healthStatusSchema`, `maskedTeaserSchema`, the holding-change status)
 * are imported from `types/market/`, never redefined.
 */
import { z } from "zod";
import { holdingStatusSchema } from "@/types/market/etf";
import { healthStatusSchema, maskedTeaserSchema } from "@/types/market/shared";

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
// GET /funds/{symbol}/theme-tags — get_etf_theme_tags (public)
// ---------------------------------------------------------------------------

export const etfThemeTagsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  tags: z.array(z.string()).default([]),
  holder_count_wan: z.number().nullable().optional(),
});
export type EtfThemeTagsResponse = z.infer<typeof etfThemeTagsResponseSchema>;

// ---------------------------------------------------------------------------
// GET /funds/{symbol}/trailing-returns — get_etf_trailing_returns (public). Mirrors
// `EtfTrailingReturns` (backend/src/schemas/capabilities.py) — 6 fixed horizons computed from
// one split/dividend-adjusted price history fetch, never yfinance's own inconsistent-unit
// `.info` return fields. A `null` horizon is real ("history doesn't reach back that far"), never
// a fetch failure — a fetch failure instead makes the whole `returns` field `null`.
// ---------------------------------------------------------------------------

export const etfTrailingReturnsSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string(),
  return_1m_pct: z.number().nullable().optional(),
  return_1q_pct: z.number().nullable().optional(),
  return_6m_pct: z.number().nullable().optional(),
  return_1y_pct: z.number().nullable().optional(),
  return_3y_pct: z.number().nullable().optional(),
  return_5y_pct: z.number().nullable().optional(),
});
export type EtfTrailingReturns = z.infer<typeof etfTrailingReturnsSchema>;

export const etfTrailingReturnsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  returns: etfTrailingReturnsSchema.nullable().optional(),
});
export type EtfTrailingReturnsResponse = z.infer<typeof etfTrailingReturnsResponseSchema>;

// ---------------------------------------------------------------------------
// GET /funds/comparison — get_fund_comparison (public). Mirrors `FundComparisonRow`/
// `GetFundComparisonOutput` (backend/src/tools/etf_overlap_tools.py) field-for-field, restricted
// to the fields the ETF 總覽 tab's own single-symbol "since inception" call actually reads —
// `fund_info`/`sector_weightings` are the multi-symbol chat-card path's concern, not this one.
// ---------------------------------------------------------------------------

export const fundComparisonRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  window_start: z.string().nullable().optional(),
  window_end: z.string().nullable().optional(),
  total_return_pct: z.number().nullable().optional(),
  price_unavailable_reason: z.string().nullable().optional(),
});
export type FundComparisonRow = z.infer<typeof fundComparisonRowSchema>;

export const fundComparisonResponseSchema = z.object({
  rows: z.array(fundComparisonRowSchema).default([]),
  methodology_note: z.string(),
});
export type FundComparisonResponse = z.infer<typeof fundComparisonResponseSchema>;

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

export const activeEtfHoldingWeightRowSchema = z.object({
  component_stock_id: z.string(),
  component_stock_name: z.string(),
  weight: z.number(),
  industry_category: z.string().nullable().optional(),
});
export type ActiveEtfHoldingWeightRow = z.infer<typeof activeEtfHoldingWeightRowSchema>;

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
  fund_size_caveat: z.string().nullable().optional(),
  premium_discount_pct: z.number().nullable().optional(),
  premium_discount_unavailable_reason: z.string().nullable().optional(),
  estimated_nav_per_unit: z.number().nullable().optional(),
  estimated_premium_discount_pct: z.number().nullable().optional(),
  estimation_caveat: z.string().nullable().optional(),
  rows: z.array(activeEtfReportRowSchema).default([]),
  current_holdings: z.array(activeEtfHoldingWeightRowSchema).default([]),
});
export type ActiveEtfReportData = z.infer<typeof activeEtfReportDataSchema>;

export const activeEtfReportResponseSchema = z.union([
  activeEtfReportDataSchema,
  maskedTeaserSchema,
]);
export type ActiveEtfReportResponse = z.infer<typeof activeEtfReportResponseSchema>;

// ---------------------------------------------------------------------------
// GET /funds/{symbol}/holdings — get_fund_holdings (public, non-TW).
// Also doubles as the US-ETF-vs-stock signal on the stock-detail page: `classifySymbolType`
// always returns "stock" for US (TW's code-shape rule doesn't apply — see
// `lib/symbol-type.ts`), so whether this call resolves non-empty `holdings`/`sector_weightings`
// is the only real signal a US symbol is a fund, not a plain equity.
// ---------------------------------------------------------------------------

export const fundHoldingRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  holding_symbol: z.string(),
  holding_name: z.string(),
  weight: z.number(),
});
export type FundHoldingRow = z.infer<typeof fundHoldingRowSchema>;

export const fundSectorWeightingRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  sector: z.string(),
  weight: z.number(),
});
export type FundSectorWeightingRow = z.infer<typeof fundSectorWeightingRowSchema>;

export const fundHoldingsResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  holdings: z.array(fundHoldingRowSchema).default([]),
  sector_weightings: z.array(fundSectorWeightingRowSchema).default([]),
});
export type FundHoldingsResponse = z.infer<typeof fundHoldingsResponseSchema>;

// ---------------------------------------------------------------------------
// GET /funds/{symbol}/profile — get_fund_profile (public, non-TW). The 總覽 tab's
// fund-equivalent of `get_company_profile` — AUM/category/issuer/YTD-return/distribution-yield/
// inception-date, fields a fund has that a plain equity's ProfileCard never asked for.
// ---------------------------------------------------------------------------

export const fundProfileSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  as_of: z.string(),
  total_assets: z.number().nullable().optional(),
  category: z.string().nullable().optional(),
  fund_family: z.string().nullable().optional(),
  ytd_return_pct: z.number().nullable().optional(),
  distribution_yield_pct: z.number().nullable().optional(),
  inception_date: z.string().nullable().optional(),
});
export type FundProfile = z.infer<typeof fundProfileSchema>;

export const fundProfileResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: healthStatusSchema,
  profile: fundProfileSchema.nullable().optional(),
});
export type FundProfileResponse = z.infer<typeof fundProfileResponseSchema>;
