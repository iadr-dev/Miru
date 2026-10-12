/**
 * Futures/options — `/market/futures-snapshot`, `/derivatives/taiex-vix`
 * (`backend/src/api/routers/market_rest.py`).
 */

import { z } from "zod";
import { maskedTeaserSchema, rowsOutputSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 23. GET /market/futures-snapshot — get_futures_snapshot (Sponsor-tier). Mirrors
//     `FuturesSnapshotRow` (backend/src/schemas/capabilities.py) field-for-field.
// ---------------------------------------------------------------------------

export const futuresSnapshotRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  change_price: z.number(),
  change_rate: z.number(),
  average_price: z.number(),
  volume: z.number(),
  total_volume: z.number(),
  amount: z.number(),
  total_amount: z.number(),
  yesterday_volume: z.number(),
  buy_price: z.number(),
  buy_volume: z.number(),
  sell_price: z.number(),
  sell_volume: z.number(),
  volume_ratio: z.number(),
  tick_type: z.string(),
});

export type FuturesSnapshotRow = z.infer<typeof futuresSnapshotRowSchema>;

export const futuresSnapshotDataSchema = rowsOutputSchema(futuresSnapshotRowSchema);

export const futuresSnapshotResponseSchema = z.union([
  futuresSnapshotDataSchema,
  maskedTeaserSchema,
]);

export type FuturesSnapshotResponse = z.infer<typeof futuresSnapshotResponseSchema>;

// ---------------------------------------------------------------------------
// 24. GET /derivatives/taiex-vix — get_taiex_vix (Sponsor-tier). Mirrors
//     `OptionVixRow` (backend/src/schemas/capabilities.py) field-for-field.
// ---------------------------------------------------------------------------

export const optionVixRowSchema = z.object({
  market: z.string(),
  trade_date: z.string(),
  time: z.string(),
  vix: z.number(),
});

export type OptionVixRow = z.infer<typeof optionVixRowSchema>;

export const optionVixDataSchema = rowsOutputSchema(optionVixRowSchema);

export const optionVixResponseSchema = z.union([optionVixDataSchema, maskedTeaserSchema]);

export type OptionVixResponse = z.infer<typeof optionVixResponseSchema>;
