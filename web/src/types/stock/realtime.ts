/**
 * 即時 tab — `/stock/live-quote` (TWSE MIS), `/stock/price-limit`, `/stock/intraday-kbar`,
 * `/stock/price-tick` (end-of-day replays, never a live stream).
 */

import { z } from "zod";
import { healthStatusSchema, maskedTeaserSchema } from "@/types/market/shared";

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
