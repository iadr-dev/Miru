/**
 * Rankings and screens — `/analytics/rankings` (composite AI score),
 * `/market/us-rankings`, `/market/tw-rankings`, `/market/tag-screener`, `/market/daily-signal`.
 */

import { z } from "zod";
import { holdingStatusSchema } from "@/types/market/etf";
import { symbolTypeSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 10. GET /analytics/rankings — get_score_leaderboard (public composite AI score)
// ---------------------------------------------------------------------------

export const SCORE_BANDS = ["偏多", "中性", "偏空"] as const;

export const scoreBandSchema = z.enum(SCORE_BANDS);

export type ScoreBand = z.infer<typeof scoreBandSchema>;

export const LEADERBOARD_DIRECTIONS = ["bullish", "bearish"] as const;

export const leaderboardDirectionSchema = z.enum(LEADERBOARD_DIRECTIONS);

export type LeaderboardDirection = z.infer<typeof leaderboardDirectionSchema>;

export const LEADERBOARD_UNIVERSES = ["stock", "etf"] as const;

export const leaderboardUniverseSchema = z.enum(LEADERBOARD_UNIVERSES);

export type LeaderboardUniverse = z.infer<typeof leaderboardUniverseSchema>;

/** One named factor's cross-sectional standing — `percentile`/`unavailable_reason` are mutually
 * exclusive (a factor missing its inputs reports why, never a fabricated percentile). */
export const factorScoreSchema = z.object({
  name: z.string(),
  percentile: z.number().nullable(),
  description: z.string(),
  unavailable_reason: z.string().nullable(),
});

export type FactorScore = z.infer<typeof factorScoreSchema>;

/** Every per-factor key is optional — a stock, a US stock, and an ETF are each scored on
 * disjoint factor sets (`analytics/score.py`'s `StockScoreBreakdown`/`UsStockScoreBreakdown`/
 * `EtfScoreBreakdown`), so a result of one `symbol_type`/market simply omits the other shapes'
 * keys rather than sending a fabricated one. `analyst_revision`/`growth` are
 * `UsStockScoreBreakdown`'s US-native factors (replacing TW-only `revenue_flow`, and rounding
 * out the six-factor US set respectively — see that model's own docstring) — a US row never
 * carries `revenue_flow`, and a TW row never carries `analyst_revision`/`growth`. */
export const scoreBreakdownSchema = z.object({
  momentum: factorScoreSchema.optional(),
  trend_quality: factorScoreSchema.optional(),
  value_guard: factorScoreSchema.optional(),
  quality: factorScoreSchema.optional(),
  revenue_flow: factorScoreSchema.optional(),
  analyst_revision: factorScoreSchema.optional(),
  growth: factorScoreSchema.optional(),
  liquidity: factorScoreSchema.optional(),
  premium_discount: factorScoreSchema.optional(),
  cost: factorScoreSchema.optional(),
  management_activity: factorScoreSchema.optional(),
  liquidity_flow: factorScoreSchema.optional(),
});

export type ScoreBreakdown = z.infer<typeof scoreBreakdownSchema>;

export const scoreResultSchema = z.object({
  symbol: z.string(),
  symbol_type: symbolTypeSchema.nullable().optional(),
  composite: z.number().nullable(),
  band: scoreBandSchema,
  breakdown: scoreBreakdownSchema.nullable().optional(),
  partial_factors: z.array(z.string()).optional(),
  regime_caveat: z.string().optional(),
  non_trading_caveat: z.string().optional(),
  normalization_caveat: z.string().optional(),
});

export type ScoreResult = z.infer<typeof scoreResultSchema>;

export const scoreLeaderboardDataSchema = z.object({
  market: z.string(),
  as_of: z.string().nullable(),
  results: z.array(scoreResultSchema).default([]),
});

export type ScoreLeaderboardData = z.infer<typeof scoreLeaderboardDataSchema>;

// ---------------------------------------------------------------------------
// 11b. GET /market/us-rankings — get_us_market_rankings (public, US-only 排行榜)
// ---------------------------------------------------------------------------

export const RANKING_KINDS = ["trending", "market_cap", "revenue", "roe", "volume"] as const;

export const rankingKindSchema = z.enum(RANKING_KINDS);

export type RankingKind = z.infer<typeof rankingKindSchema>;

export const marketRankingRowSchema = z.object({
  symbol: z.string(),
  name: z.string().nullable(),
  price: z.number().nullable(),
  change: z.number().nullable(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
});

export type MarketRankingRow = z.infer<typeof marketRankingRowSchema>;

export const usMarketRankingsResponseSchema = z.object({
  kind: rankingKindSchema,
  rows: z.array(marketRankingRowSchema).default([]),
  detail: z.string(),
});

export type UsMarketRankingsResponse = z.infer<typeof usMarketRankingsResponseSchema>;

// ---------------------------------------------------------------------------
// 11c. GET /market/tw-rankings — get_tw_market_rankings (public, TW-only 排行榜, 6 of its 7 tabs
// — 大戶買賣 reuses holderAccumulationResponseSchema below verbatim instead of this schema)
// ---------------------------------------------------------------------------

export const TW_RANKING_KINDS = [
  "dealer_streak",
  "trust_streak",
  "foreign_streak",
  "margin_change",
  "dividend_yield",
  "revenue_growth",
] as const;

export const twRankingKindSchema = z.enum(TW_RANKING_KINDS);

export type TwRankingKind = z.infer<typeof twRankingKindSchema>;

// Same `SymbolQuoteRows`-compatible base as `marketRankingRowSchema` above, plus one shared
// `extra_value`/`extra_label` pair carrying each tab's own distinct metric (signed streak-day
// count, signed margin-balance change, or a plain percentage) — see the backend
// `TwRankingRow`'s own docstring for why one shared numeric field covers all 6 kinds.
export const twRankingRowSchema = z.object({
  symbol: z.string(),
  name: z.string().nullable(),
  price: z.number().nullable(),
  change: z.number().nullable(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
  extra_value: z.number(),
  extra_label: z.string(),
});

export type TwRankingRow = z.infer<typeof twRankingRowSchema>;

export const twMarketRankingsResponseSchema = z.object({
  kind: twRankingKindSchema,
  trade_date: z.string().nullable().optional(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(twRankingRowSchema).default([]),
});

export type TwMarketRankingsResponse = z.infer<typeof twMarketRankingsResponseSchema>;

// ---------------------------------------------------------------------------
// 11d. GET /market/tag-screener — run_tag_screener (public). Mirrors `TagScreenerResult`
//      (backend/src/schemas/screener.py) field-for-field.
// ---------------------------------------------------------------------------

export const tagScreenerHitSchema = z.object({
  symbol: z.string(),
  matched_tags: z.array(z.string()),
});

export type TagScreenerHit = z.infer<typeof tagScreenerHitSchema>;

export const tagScreenerResultSchema = z.object({
  market: z.string(),
  as_of: z.string().nullable().optional(),
  total_matches: z.number(),
  hits: z.array(tagScreenerHitSchema),
  detail: z.string(),
});

export type TagScreenerResult = z.infer<typeof tagScreenerResultSchema>;

export const tagScreenerResponseSchema = z.object({ result: tagScreenerResultSchema });

export type TagScreenerResponse = z.infer<typeof tagScreenerResponseSchema>;

export const holderThresholdSchema = z.union([
  z.literal(200),
  z.literal(400),
  z.literal(800),
  z.literal(1000),
]);

export type HolderThreshold = z.infer<typeof holderThresholdSchema>;

export const holderWindowSchema = z.union([z.literal(1), z.literal(2), z.literal(3), z.literal(4)]);

export type HolderWindow = z.infer<typeof holderWindowSchema>;

export const holderAccumulationRowSchema = z.object({
  rank: z.number().default(0),
  symbol: z.string().nullable(),
  stock_name: z.string().nullable(),
  latest_close: z.number().nullable(),
  percent_of_custody_before: z.number().nullable(),
  percent_of_custody_after: z.number().nullable(),
  percent_of_custody_change: z.number().nullable(),
  shares_change: z.number().nullable(),
  locked: z.boolean().default(false),
});

export type HolderAccumulationRow = z.infer<typeof holderAccumulationRowSchema>;

export const holderAccumulationDataSchema = z.object({
  market: z.string(),
  threshold: holderThresholdSchema,
  window: holderWindowSchema,
  as_of: z.string().nullable(),
  period_ago: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  free_row_count: z.number().default(0),
  rows: z.array(holderAccumulationRowSchema).default([]),
});

export type HolderAccumulationData = z.infer<typeof holderAccumulationDataSchema>;

// No longer unioned with `maskedTeaserSchema` — `get_holder_accumulation` opted into
// governance.md §4's row-preview exception (`supports_row_preview=True`), so an unentitled
// caller gets this shape directly with `locked` rows, never a whole-tool `MaskedTeaser`.
export const holderAccumulationResponseSchema = holderAccumulationDataSchema;

export type HolderAccumulationResponse = z.infer<typeof holderAccumulationResponseSchema>;

// ---------------------------------------------------------------------------
// 17. GET /market/daily-signal — get_daily_signal (Sponsor-tier). Mirrors
//     `DailySignalRow`/`DailySignalHighlight`/`GetDailySignalOutput`
//     (backend/src/tools/market/) field-for-field.
// ---------------------------------------------------------------------------

export const dailySignalRowSchema = z.object({
  rank: z.number().default(0),
  component_stock_id: z.string().nullable(),
  component_stock_name: z.string().nullable(),
  close: z.number().nullable(),
  change_pct: z.number().nullable(),
  status: holdingStatusSchema,
  shares_delta: z.number().nullable(),
  pct_change: z.number().nullable(),
  buying_etf_count: z.number().nullable(),
  selling_etf_count: z.number().nullable(),
  locked: z.boolean().default(false),
});

export type DailySignalRow = z.infer<typeof dailySignalRowSchema>;

export const dailySignalHighlightSchema = z.object({
  component_stock_id: z.string().nullable(),
  component_stock_name: z.string().nullable(),
  held_market_value_delta: z.number().nullable(),
  buying_etf_count: z.number().nullable(),
  selling_etf_count: z.number().nullable(),
  locked: z.boolean().default(false),
});

export type DailySignalHighlight = z.infer<typeof dailySignalHighlightSchema>;

export const dailySignalDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  prior_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  total_stock_count: z.number().default(0),
  new_count: z.number().default(0),
  removed_count: z.number().default(0),
  increased_count: z.number().default(0),
  decreased_count: z.number().default(0),
  free_row_count: z.number().default(0),
  top_inflow: dailySignalHighlightSchema.nullable(),
  top_outflow: dailySignalHighlightSchema.nullable(),
  rows: z.array(dailySignalRowSchema).default([]),
});

// No longer unioned with `maskedTeaserSchema` — `get_daily_signal` opted into governance.md §4's
// row-preview exception (`supports_row_preview=True`), so an unentitled caller gets this shape
// directly with `locked` rows/highlights, never a whole-tool `MaskedTeaser`.
export const dailySignalResponseSchema = dailySignalDataSchema;

export type DailySignalResponse = z.infer<typeof dailySignalResponseSchema>;
