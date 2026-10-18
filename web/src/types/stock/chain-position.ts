/**
 * Stock chain position — `GET /symbols/{symbol}/chain-positions` (public): where a company sits
 * (upstream / midstream / downstream) in each industry chain it belongs to, with the reasons.
 * An empty `positions` array means "not classified yet" (尚未分類), never a guess.
 */

import { z } from "zod";

export const chainPositionTierSchema = z.enum(["upstream", "midstream", "downstream"]);

export const chainPositionMethodSchema = z.enum(["override", "evidence", "prior"]);

export const chainPositionReasonSchema = z.object({
  // Open string on purpose: an unknown code falls back to a generic label in the UI.
  code: z.string(),
  detail: z.string().nullable().optional(),
});

export const chainPositionSchema = z.object({
  chain_key: z.string(),
  chain_name: z.string(),
  position: chainPositionTierSchema,
  is_primary: z.boolean(),
  method: chainPositionMethodSchema,
  confidence: z.number().min(0).max(1),
  reasons: z.array(chainPositionReasonSchema).default([]),
  computed_at: z.string().nullable(),
});

export type ChainPosition = z.infer<typeof chainPositionSchema>;
export type ChainPositionTier = z.infer<typeof chainPositionTierSchema>;

export const chainPositionsResponseSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  positions: z.array(chainPositionSchema).default([]),
});

export type ChainPositionsResponse = z.infer<typeof chainPositionsResponseSchema>;
