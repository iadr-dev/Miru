/**
 * Envelope pieces every market/stock/ETF contract shares: the Sponsor-tier `MaskedTeaser`
 * lock (always HTTP 200, web/OVERVIEW.md §4/§8), the source-health status, and the `_RowsOutput`
 * row-list shape (`backend/src/tools/base.py`). Also the stock/ETF symbol-type enum.
 */

import { z } from "zod";
import { TIERS } from "@/types/account";

// ---------------------------------------------------------------------------
// Shared envelope pieces
// ---------------------------------------------------------------------------

/** `tools/base.py::MaskedTeaser` — the Sponsor-tier lock envelope. Always HTTP 200. */
export const maskedTeaserSchema = z.object({
  locked: z.literal(true),
  required_tier: z.enum(TIERS.filter((t) => t !== "free") as ["premium", "pro"]),
  preview: z.string(),
  tool_name: z.string(),
});

export type MaskedTeaser = z.infer<typeof maskedTeaserSchema>;

/** Narrows a Sponsor-tier union response down to the masked-teaser branch, if present — every
 * card component branches on this once rather than re-deriving the `locked` check per field. */
export function isMaskedTeaser(value: { locked?: true } | object): value is MaskedTeaser {
  return "locked" in value && value.locked === true;
}

export const HEALTH_STATUSES = [
  "ok",
  "stale",
  "unreachable",
  "unavailable-tier",
  "unavailable",
] as const;

export const healthStatusSchema = z.enum(HEALTH_STATUSES);

/** The `_RowsOutput` shape every non-bundled tool output subclasses (`source_id` +
 * `currentness_caveat` + `health_status` + `rows`). */
export function rowsOutputSchema<RowSchema extends z.ZodTypeAny>(rowSchema: RowSchema) {
  return z.object({
    source_id: z.string(),
    currentness_caveat: z.string(),
    health_status: healthStatusSchema,
    rows: z.array(rowSchema).default([]),
  });
}

export const SYMBOL_TYPES = ["stock", "etf", "etf_leveraged", "etf_inverse"] as const;

export const symbolTypeSchema = z.enum(SYMBOL_TYPES);

export type SymbolType = z.infer<typeof symbolTypeSchema>;
