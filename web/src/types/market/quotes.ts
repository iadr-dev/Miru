/**
 * Quotes and price series — `/market/symbol-info`, `/market/price-history`, `/market/quotes`,
 * `/market/index-strip`, `/market/tick-snapshot` (`backend/src/api/routers/market_rest.py`).
 */

import { z } from "zod";
import { maskedTeaserSchema, rowsOutputSchema } from "@/types/market/shared";

// ---------------------------------------------------------------------------
// 9. GET /market/symbol-info + /market/price-history — the shared batched
//    per-symbol enrichment `useSymbolQuotes` reads (public).
// ---------------------------------------------------------------------------

export const symbolInfoRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  name: z.string(),
  industry_category: z.string().nullable(),
});

export type SymbolInfoRow = z.infer<typeof symbolInfoRowSchema>;

// `GetSymbolInfoOutput`/`GetPriceHistoryOutput` are `{market, ..., data_available,
// unavailable_reason, rows}` — a distinct shape from `rowsOutputSchema`'s
// `{source_id, currentness_caveat, health_status, rows}` (those two backend tools never route
// through a `SourceResult`, they read the ingest-owned Parquet store directly).
export const symbolInfoResponseSchema = z.object({
  market: z.string(),
  roster_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(symbolInfoRowSchema).default([]),
});

export type SymbolInfoResponse = z.infer<typeof symbolInfoResponseSchema>;

export const priceHistoryBarSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  open: z.number(),
  high: z.number(),
  low: z.number(),
  close: z.number(),
  volume: z.number(),
});

export type PriceHistoryBar = z.infer<typeof priceHistoryBarSchema>;

export const priceHistoryResponseSchema = z.object({
  market: z.string(),
  start_date: z.string().nullable(),
  end_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(priceHistoryBarSchema).default([]),
});

export type PriceHistoryResponse = z.infer<typeof priceHistoryResponseSchema>;

// GET /market/quotes — get_symbol_quotes (backend/src/tools/market/). The list-row view:
// `change`/`change_pct` are always the DAILY move, computed server-side; the sparkline window is
// separate. A requested symbol with no data still gets a row with null fields.
export const symbolQuoteRowSchema = z.object({
  symbol: z.string(),
  name: z.string().nullable(),
  industry_category: z.string().nullable(),
  trade_date: z.string().nullable(),
  close: z.number().nullable(),
  prev_close: z.number().nullable(),
  change: z.number().nullable(),
  change_pct: z.number().nullable(),
  volume: z.number().nullable(),
  sparkline: z.array(z.number()).default([]),
});

export type SymbolQuoteRow = z.infer<typeof symbolQuoteRowSchema>;

export const symbolQuotesResponseSchema = z.object({
  market: z.string(),
  end_date: z.string().nullable(),
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable().optional(),
  rows: z.array(symbolQuoteRowSchema).default([]),
});

export type SymbolQuotesResponse = z.infer<typeof symbolQuotesResponseSchema>;

// ---------------------------------------------------------------------------
// 21. GET /market/index-strip — get_market_index_strip (public). Mirrors
//     `MarketIndexStripItemRow`/`GetMarketIndexStripOutput`
//     (backend/src/tools/market_index_strip_tools.py) field-for-field.
// ---------------------------------------------------------------------------

export const marketIndexStripItemSchema = z.object({
  key: z.string(),
  label: z.string(),
  value: z.number(),
  change: z.number().nullable(),
  change_pct: z.number().nullable(),
  as_of: z.string(),
});

export type MarketIndexStripItem = z.infer<typeof marketIndexStripItemSchema>;

export const marketIndexStripDataSchema = z.object({
  rows: z.array(marketIndexStripItemSchema).default([]),
});

// Public — never Sponsor-masked, same un-unioned convention as `dividendCalendarResponseSchema`.
export const marketIndexStripResponseSchema = marketIndexStripDataSchema;

export type MarketIndexStripResponse = z.infer<typeof marketIndexStripResponseSchema>;

// ---------------------------------------------------------------------------
// 22. GET /market/tick-snapshot — get_tick_snapshot (Sponsor-tier). Mirrors
//     `TickSnapshotRow` (backend/src/schemas/capabilities.py) field-for-field.
// ---------------------------------------------------------------------------

export const tickSnapshotRowSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  trade_date: z.string(),
  amount: z.number(),
  average_price: z.number(),
  buy_price: z.number(),
  buy_volume: z.number(),
  change_price: z.number(),
  change_rate: z.number(),
  close: z.number(),
  high: z.number(),
  low: z.number(),
  open: z.number(),
  sell_price: z.number(),
  sell_volume: z.number(),
  total_amount: z.number(),
  total_volume: z.number(),
  volume: z.number(),
  volume_ratio: z.number(),
  yesterday_volume: z.number(),
  tick_type: z.string(),
});

export type TickSnapshotRow = z.infer<typeof tickSnapshotRowSchema>;

export const tickSnapshotDataSchema = rowsOutputSchema(tickSnapshotRowSchema);

export const tickSnapshotResponseSchema = z.union([tickSnapshotDataSchema, maskedTeaserSchema]);

export type TickSnapshotResponse = z.infer<typeof tickSnapshotResponseSchema>;
