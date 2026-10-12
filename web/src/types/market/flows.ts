/**
 * Whole-market money flows — `/market/institutional-ranking`, `/market/margin-maintenance`,
 * `/market/etf-money-flow`, `/market/industry-flow`,
 * `/market/margin-short-balance` (`backend/src/api/routers/market_rest.py`). Row field names
 * mirror `backend/src/schemas/capabilities.py`.
 */

import { z } from "zod";
import { maskedTeaserSchema, rowsOutputSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 1. GET /market/institutional-ranking — get_institutional_flow_total_market (public)
// ---------------------------------------------------------------------------

export const institutionalFlowTotalMarketRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  name: z.string(),
  buy: z.number(),
  sell: z.number(),
});

export type InstitutionalFlowTotalMarketRow = z.infer<typeof institutionalFlowTotalMarketRowSchema>;

export const institutionalRankingResponseSchema = rowsOutputSchema(
  institutionalFlowTotalMarketRowSchema,
);

export type InstitutionalRankingResponse = z.infer<typeof institutionalRankingResponseSchema>;

// ---------------------------------------------------------------------------
// 2. GET /market/margin-maintenance — get_margin_maintenance (Sponsor-tier)
// ---------------------------------------------------------------------------

export const marginMaintenanceRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  total_exchange_margin_maintenance: z.number(),
});

export type MarginMaintenanceRow = z.infer<typeof marginMaintenanceRowSchema>;

export const marginMaintenanceDataSchema = rowsOutputSchema(marginMaintenanceRowSchema);

export const marginMaintenanceResponseSchema = z.union([
  marginMaintenanceDataSchema,
  maskedTeaserSchema,
]);

export type MarginMaintenanceResponse = z.infer<typeof marginMaintenanceResponseSchema>;

// ---------------------------------------------------------------------------
// 4. GET /market/etf-money-flow — get_etf_money_flow (Sponsor-tier)
// ---------------------------------------------------------------------------

export const etfMoneyFlowRowSchema = z.object({
  rank: z.number().default(0),
  component_stock_id: z.string().nullable(),
  component_stock_name: z.string().nullable(),
  active_etf_holder_count: z.number().nullable().optional(),
  held_shares: z.number().nullable().optional(),
  held_market_value: z.number().nullable().optional(),
  stock_shares_held_pct: z.number().nullable().optional(),
  net_shares_flow: z.number().nullable(),
  buying_etf_count: z.number().nullable(),
  selling_etf_count: z.number().nullable(),
  locked: z.boolean().default(false),
});

export type EtfMoneyFlowRow = z.infer<typeof etfMoneyFlowRowSchema>;

export const etfMoneyFlowDataSchema = z.object({
  market: z.string(),
  start_date: z.string(),
  end_date: z.string(),
  holding_as_of: z.string().nullable().optional(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  total_stock_count: z.number().default(0),
  stock_shares_held_pct_caveat: z.string(),
  free_row_count: z.number().default(0),
  inflow_ranking: z.array(etfMoneyFlowRowSchema).default([]),
  outflow_ranking: z.array(etfMoneyFlowRowSchema).default([]),
});

// No longer unioned with `maskedTeaserSchema` — `get_etf_money_flow` opted into governance.md
// §4's row-preview exception (`supports_row_preview=True`), so an unentitled caller gets this
// shape directly with `locked` rows, never a whole-tool `MaskedTeaser`.
export const etfMoneyFlowResponseSchema = etfMoneyFlowDataSchema;

export type EtfMoneyFlowResponse = z.infer<typeof etfMoneyFlowResponseSchema>;

// ---------------------------------------------------------------------------
// 5. GET /market/industry-flow — get_industry_money_flow (Sponsor-tier)
// ---------------------------------------------------------------------------

export const industryChainMoneyFlowRowSchema = z.object({
  rank: z.number().default(0),
  market: z.string(),
  trade_date: z.string(),
  is_total: z.boolean().default(false),
  industry: z.string().nullable(),
  sub_industry: z.string().nullable(),
  stock_count: z.number().nullable(),
  trading_volume: z.number().nullable(),
  trading_money: z.number().nullable(),
  trading_money_pct: z.number().nullable(),
  locked: z.boolean().default(false),
});

export type IndustryChainMoneyFlowRow = z.infer<typeof industryChainMoneyFlowRowSchema>;

export const industryFlowDataSchema = rowsOutputSchema(industryChainMoneyFlowRowSchema).extend({
  free_row_count: z.number().default(0),
});

// No longer unioned with `maskedTeaserSchema` — `get_industry_money_flow` opted into
// governance.md §4's row-preview exception (`supports_row_preview=True`), so an unentitled
// caller gets this shape directly with `locked` rows, never a whole-tool `MaskedTeaser`.
export const industryFlowResponseSchema = industryFlowDataSchema;

export type IndustryFlowResponse = z.infer<typeof industryFlowResponseSchema>;

// ---------------------------------------------------------------------------
// 15. GET /market/margin-short-balance — get_margin_short_total_market (Sponsor-tier)
// ---------------------------------------------------------------------------

export const marginShortTotalMarketRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  name: z.string(),
  buy: z.number(),
  sell: z.number(),
  today_balance: z.number(),
  // FinMind's own field name — trailing underscore avoids shadowing the JS reserved word.
  return_: z.number(),
});

export type MarginShortTotalMarketRow = z.infer<typeof marginShortTotalMarketRowSchema>;

export const marginShortBalanceDataSchema = rowsOutputSchema(marginShortTotalMarketRowSchema);

export const marginShortBalanceResponseSchema = z.union([
  marginShortBalanceDataSchema,
  maskedTeaserSchema,
]);

export type MarginShortBalanceResponse = z.infer<typeof marginShortBalanceResponseSchema>;
