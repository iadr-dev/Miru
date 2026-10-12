/**
 * Company disclosures — `/market/material-announcement-feed`,
 * `/market/insider-transfer-declarations`, `/market/disposition-board`.
 */

import { z } from "zod";
import { healthStatusSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 19. GET /market/material-announcement-feed — get_material_announcement_feed (public,
//     whole-market 重大訊息 feed). Mirrors `MaterialAnnouncementRow`/
//     `GetMaterialAnnouncementFeedOutput` (backend/src/tools/mops_tools.py) field-for-field.
//     Distinct from the existing per-symbol material-announcement shape (unused by any
//     frontend component today) — this is the whole-market list-view shape with a derived
//     `category` per row.
// ---------------------------------------------------------------------------

export const materialAnnouncementRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  announced_date: z.string(),
  announced_time: z.string(),
  subject: z.string(),
  detail: z.string(),
  qualifying_clause: z.string().nullable(),
  category: z.string(),
});

export type MaterialAnnouncementRow = z.infer<typeof materialAnnouncementRowSchema>;

export const materialAnnouncementFeedDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  category_counts: z.record(z.string(), z.number()).default({}),
  available_dates: z.array(z.string()).default([]),
  rows: z.array(materialAnnouncementRowSchema).default([]),
});

export type MaterialAnnouncementFeedData = z.infer<typeof materialAnnouncementFeedDataSchema>;

// Public — never Sponsor-masked, matching `dividendCalendarResponseSchema`'s own un-unioned shape.
export const materialAnnouncementFeedResponseSchema = materialAnnouncementFeedDataSchema;

export type MaterialAnnouncementFeedResponse = z.infer<
  typeof materialAnnouncementFeedResponseSchema
>;

// ---------------------------------------------------------------------------
// 20. GET /market/insider-transfer-declarations — get_insider_transfer_declarations (public,
//     whole-market 內部人股權轉讓事前申報 feed, t187ap12_L only). Mirrors
//     `InsiderTransferDeclarationRow`/`GetInsiderTransferDeclarationOutput`
//     (backend/src/tools/mops_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const insiderTransferDeclarationRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  company_name: z.string(),
  filer_role: z.string(),
  person_name: z.string(),
  transfer_method: z.string(),
  transfer_shares: z.number(),
  max_daily_exchange_shares: z.number().nullable(),
  transferee: z.string().nullable(),
  current_shares_own: z.number(),
  current_shares_trust: z.number(),
  planned_shares_own: z.number(),
  planned_shares_trust: z.number(),
  projected_shares_own_after: z.number(),
  projected_shares_trust_after: z.number(),
  transfer_window_start: z.string(),
  transfer_window_end: z.string(),
});

export type InsiderTransferDeclarationRow = z.infer<typeof insiderTransferDeclarationRowSchema>;

export const insiderTransferDeclarationDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  available_dates: z.array(z.string()).default([]),
  rows: z.array(insiderTransferDeclarationRowSchema).default([]),
});

export type InsiderTransferDeclarationData = z.infer<typeof insiderTransferDeclarationDataSchema>;

// Public — never Sponsor-masked, matching `dividendCalendarResponseSchema`'s own un-unioned shape.
export const insiderTransferDeclarationResponseSchema = insiderTransferDeclarationDataSchema;

export type InsiderTransferDeclarationResponse = z.infer<
  typeof insiderTransferDeclarationResponseSchema
>;

// ---------------------------------------------------------------------------
// 25. GET /market/disposition-board — get_disposition_board (Sponsor-tier). Mirrors
//     `DispositionBoardCardRow`/`GetDispositionBoardOutput`
//     (backend/src/tools/disposition_board_tools.py, backend/src/schemas/capabilities.py)
//     field-for-field. 3 buckets this pass (at_risk/in_disposition/exiting_soon) — 自結發布 is
//     out of scope (see the backend plan this mirrors).
// ---------------------------------------------------------------------------

export const DISPOSITION_BUCKETS = ["at_risk", "in_disposition", "exiting_soon"] as const;

export const dispositionBucketSchema = z.enum(DISPOSITION_BUCKETS);

export type DispositionBucket = z.infer<typeof dispositionBucketSchema>;

export const dispositionBoardCardRowSchema = z.object({
  rank: z.number().default(0),
  symbol: z.string().nullable(),
  market: z.string(),
  company_name: z.string().nullable(),
  security_type: z.string().nullable(),
  close: z.number().nullable(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
  turnover: z.number().nullable(),
  institutional_net: z.number().nullable(),
  margin_ratio: z.number().nullable(),
  margin_caveat: z.string().nullable(),
  margin_health_status: healthStatusSchema.nullable(),
  day_trading_ratio: z.number().nullable(),
  bucket: dispositionBucketSchema,
  disposition_measure: z.string().nullable(),
  disposition_period_start: z.string().nullable(),
  disposition_period_end: z.string().nullable(),
  matching_interval_minutes: z.number().nullable(),
  flags: z.array(z.string()).default([]),
  attention_flags_remaining: z.number().nullable(),
  estimated_days_until_disposition: z.number().nullable(),
  days_until_exiting: z.number().nullable(),
  projection_caveat: z.string(),
  locked: z.boolean().default(false),
});

export type DispositionBoardCardRow = z.infer<typeof dispositionBoardCardRowSchema>;

export const dispositionBoardDataSchema = z.object({
  market: z.string(),
  report_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  bucket_counts: z.record(z.string(), z.number()).default({}),
  free_row_count: z.number().default(0),
  cards: z.array(dispositionBoardCardRowSchema).default([]),
});

export type DispositionBoardData = z.infer<typeof dispositionBoardDataSchema>;

// No longer unioned with `maskedTeaserSchema` — `get_disposition_board` opted into
// governance.md §4's row-preview exception (`supports_row_preview=True`), so an unentitled
// caller gets this shape directly with `locked` cards, never a whole-tool `MaskedTeaser`.
export const dispositionBoardResponseSchema = dispositionBoardDataSchema;

export type DispositionBoardResponse = z.infer<typeof dispositionBoardResponseSchema>;
