/**
 * AI tab — `/analytics/{symbol}/score`, `/analytics/{symbol}/trend-score`,
 * `/analytics/{symbol}/summary` (`backend/src/api/routers/analytics.py`).
 */

import { z } from "zod";
import { scoreResultSchema } from "@/types/market/rankings";
import { maskedTeaserSchema } from "@/types/market/shared";

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
  // True only when the caller's tier gates this dimension (分點 is Sponsor-tier); a null value
  // with `locked: false` means the data simply isn't there yet.
  locked: z.boolean().default(false),
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
