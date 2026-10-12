/**
 * Who holds the stock — `/stock/insider-shareholding`, `/stock/insider-transfer-declarations`,
 * `/stock/foreign-shareholding`, `/stock/shareholder-dispersion`, `/stock/etfs-holding-stock`.
 */

import { z } from "zod";
import { insiderTransferDeclarationRowSchema } from "@/types/market/disclosures";
import { healthStatusSchema, maskedTeaserSchema } from "@/types/market/shared";

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
//     (public), filtered server-side to one symbol. Same row as the whole-market feed
//     (`InsiderTransferDeclarationRow`, backend/src/schemas/capabilities.py), so it reuses
//     `types/market/disclosures.ts`'s schema. Whole-market feed, ingest-owned Parquet store —
//     `data_available: false` when ingest has no partition yet for the requested day, distinct
//     from "available but zero rows for this symbol" (data_available stays true, rows is empty).
// ---------------------------------------------------------------------------

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
// `GetEtfsHoldingStockOutput`/`tools/market/` field-for-field. 追蹤 tab's ETF持有 card
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
