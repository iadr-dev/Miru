/**
 * Market breadth — `/market/advance-decline`, `/market/index-contribution`.
 */

import { z } from "zod";

// ---------------------------------------------------------------------------
// 13. GET /market/advance-decline — get_advance_decline (public breadth histogram)
// ---------------------------------------------------------------------------

export const advanceDeclineBucketsSchema = z.object({
  down_over_5pct: z.number(),
  down_3_to_5pct: z.number(),
  down_2_to_3pct: z.number(),
  down_1_to_2pct: z.number(),
  down_0_to_1pct: z.number(),
  unchanged: z.number(),
  up_0_to_1pct: z.number(),
  up_1_to_2pct: z.number(),
  up_2_to_3pct: z.number(),
  up_3_to_5pct: z.number(),
  up_over_5pct: z.number(),
});

export type AdvanceDeclineBuckets = z.infer<typeof advanceDeclineBucketsSchema>;

export const advanceDeclineResponseSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  data_available: z.boolean(),
  advancing_count: z.number(),
  declining_count: z.number(),
  unchanged_count: z.number(),
  buckets: advanceDeclineBucketsSchema,
});

export type AdvanceDeclineResponse = z.infer<typeof advanceDeclineResponseSchema>;

// ---------------------------------------------------------------------------
// 14. GET /market/index-contribution — get_index_contribution (Sponsor-tier,
//     APPROXIMATE market-cap-weight x price-change estimate — never TWSE's official
//     float-adjusted figure; see `contribution_caveat`).
// ---------------------------------------------------------------------------

export const indexContributionRowSchema = z.object({
  rank: z.number().default(0),
  symbol: z.string().nullable(),
  stock_name: z.string().nullable(),
  weight_per: z.number().nullable(),
  pct_change: z.number().nullable(),
  contribution_estimate: z.number().nullable(),
  locked: z.boolean().default(false),
});

export type IndexContributionRow = z.infer<typeof indexContributionRowSchema>;

export const indexContributionDataSchema = z.object({
  market: z.string(),
  trade_date: z.string().nullable(),
  prior_trade_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  contribution_caveat: z.string(),
  free_row_count: z.number().default(0),
  positive_contributors: z.array(indexContributionRowSchema).default([]),
  negative_contributors: z.array(indexContributionRowSchema).default([]),
});

export type IndexContributionData = z.infer<typeof indexContributionDataSchema>;

// No longer unioned with `maskedTeaserSchema` — `get_index_contribution` opted into
// governance.md §4's row-preview exception (`supports_row_preview=True`), so an unentitled
// caller gets this shape directly with `locked` rows, never a whole-tool `MaskedTeaser`.
export const indexContributionResponseSchema = indexContributionDataSchema;

export type IndexContributionResponse = z.infer<typeof indexContributionResponseSchema>;
