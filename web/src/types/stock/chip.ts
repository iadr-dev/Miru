/**
 * 籌碼 (chip) flows — `/stock/institutional-flow`, `/stock/margin-short`,
 * `/stock/securities-lending`, `/stock/chip-flows`, `/stock/government-bank-flows`,
 * `/stock/day-trading`.
 */

import { z } from "zod";
import { healthStatusSchema, maskedTeaserSchema } from "@/types/market/shared";

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
