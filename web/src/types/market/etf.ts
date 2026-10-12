/**
 * ETF lists — `/market/etf-list`, `/market/dividend-calendar`, plus the list table's sort
 * state (frontend-only).
 */

import { z } from "zod";
import { symbolTypeSchema } from "@/types/market/shared";

/** An ETF holding's change since the prior report (新增/刪除/加碼/減碼) — shared by the
 * `/market/daily-signal` rows and the ETF page's `/market/active-etf-report`. */
export const HOLDING_STATUSES = ["新增", "刪除", "加碼", "減碼", "no_prior_data"] as const;
export const holdingStatusSchema = z.enum(HOLDING_STATUSES);
export type HoldingStatus = z.infer<typeof holdingStatusSchema>;

// ---------------------------------------------------------------------------
// 16. GET /market/etf-list — get_etf_list (public whole-market ETF list). Every field
//     mirrors `EtfListRow` (backend/src/tools/market/) field-for-field — a backend
//     rename becomes a compile error here, not a silent runtime mismatch.
// ---------------------------------------------------------------------------

export const ETF_DISTRIBUTION_FREQUENCIES = ["月配", "季配", "半年配", "年配", "不配息"] as const;

export const etfDistributionFrequencySchema = z.enum(ETF_DISTRIBUTION_FREQUENCIES).nullable();

export type EtfDistributionFrequency = z.infer<typeof etfDistributionFrequencySchema>;

export const etfListRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  name: z.string(),
  close: z.number().nullable(),
  change_abs: z.number().nullable(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
  turnover: z.number().nullable(),
  return_1w_pct: z.number().nullable(),
  distribution_yield_ttm_pct: z.number().nullable(),
  distribution_frequency: etfDistributionFrequencySchema,
  aum: z.number().nullable(),
  expense_ratio_pct_approx: z.number().nullable(),
  symbol_type: symbolTypeSchema,
  is_active_etf: z.boolean().default(false),
});

export type EtfListRow = z.infer<typeof etfListRowSchema>;

export const etfListDataSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  currentness_caveat: z.string(),
  aum_caveat: z.string(),
  expense_ratio_caveat: z.string(),
  distribution_caveat: z.string(),
  rows: z.array(etfListRowSchema).default([]),
});

export type EtfListData = z.infer<typeof etfListDataSchema>;

// Public — never Sponsor-masked, matching `priceMomentumResponseSchema`'s own un-unioned shape.
export const etfListResponseSchema = etfListDataSchema;

export type EtfListResponse = z.infer<typeof etfListResponseSchema>;

// ---------------------------------------------------------------------------
// 18. GET /market/dividend-calendar — get_dividend_calendar (public, ETF-only 股息日曆). Every
//     field mirrors `DividendCalendarEventRow` (backend/src/tools/market/) field-for-
//     field — a backend rename becomes a compile error here, not a silent runtime mismatch.
// ---------------------------------------------------------------------------

export const dividendCalendarEventRowSchema = z.object({
  symbol: z.string(),
  name: z.string(),
  ex_dividend_date: z.string(),
  last_buy_date: z.string(),
  payment_date: z.string().nullable(),
  cash_dividend: z.number(),
});

export type DividendCalendarEventRow = z.infer<typeof dividendCalendarEventRowSchema>;

export const dividendCalendarDataSchema = z.object({
  market: z.string(),
  year: z.number(),
  month: z.number(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  events: z.array(dividendCalendarEventRowSchema).default([]),
});

export type DividendCalendarData = z.infer<typeof dividendCalendarDataSchema>;

// Public — never Sponsor-masked, matching `etfListResponseSchema`'s own un-unioned shape.
export const dividendCalendarResponseSchema = dividendCalendarDataSchema;

export type DividendCalendarResponse = z.infer<typeof dividendCalendarResponseSchema>;

// Frontend-invented UI-only shape (no backend citation needed, web/OVERVIEW.md §2) — the
// sortable columns `EtfListTable` lets a caller sort by, and the current sort direction.
export const ETF_SORT_KEYS = [
  "name",
  "close",
  "change_pct",
  "turnover",
  "return_1w_pct",
  "distribution_yield_ttm_pct",
  "aum",
  "expense_ratio_pct_approx",
] as const;

export type EtfSortKey = (typeof ETF_SORT_KEYS)[number];

export type SortDirection = "asc" | "desc";
