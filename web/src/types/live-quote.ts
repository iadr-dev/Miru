/**
 * The `quote` SSE frame `GET /quote/stream` publishes
 * (`backend/src/events/live_quote.py::QuotePoller.poll_tick`) — a flat frame, NOT the chat
 * turn's `sseEnvelopeSchema` wrapper (`types/sse.ts`): `{symbol, market, quote, as_of,
 * market_open}`, where `quote` is whichever one-shot quote tool's own `model_dump()` the poller
 * dispatched through (`get_price_quote`'s `{bar}` shape for non-US, `get_live_price_quote`'s
 * `{tick}` shape for US — `backend/src/api/quote_stream.py::quote_tool_name_for`). Only the US
 * `tick` shape is modeled here since that's this schema's only current consumer
 * (`hooks/use-live-quote.ts`); a TW/ETF consumer would need its own `bar`-shaped schema, not
 * built yet since `AssetHeader` still reads TW/ETF price via `getPriceHistory`.
 */
import { z } from "zod";

export const livePriceTickSchema = z.object({
  price: z.number().nullable().optional(),
  change: z.number().nullable().optional(),
  change_percent: z.number().nullable().optional(),
  day_high: z.number().nullable().optional(),
  day_low: z.number().nullable().optional(),
  day_volume: z.number().nullable().optional(),
  open_price: z.number().nullable().optional(),
  previous_close: z.number().nullable().optional(),
  bid: z.number().nullable().optional(),
  bid_size: z.number().nullable().optional(),
  ask: z.number().nullable().optional(),
  ask_size: z.number().nullable().optional(),
  currency: z.string().nullable().optional(),
  market_hours: z.number().nullable().optional(),
  observed_at: z.string().nullable().optional(),
});
export type LivePriceTick = z.infer<typeof livePriceTickSchema>;

// `GET /stock/live-price-quote` — the one-shot REST read `AssetHeader` falls back to on page
// load (the SSE poller only fetches while the market session is open, so a page load outside
// market hours needs this instead to show a last-known price via the adapter's `.info`
// snapshot fallback — see `sources/yfinance/live_quote_adapter.py::_snapshot_tick`).
export const livePriceQuoteResponseSchema = z.object({
  source_id: z.string(),
  currentness_caveat: z.string(),
  health_status: z.string(),
  tick: livePriceTickSchema.nullable().optional(),
});
export type LivePriceQuoteResponse = z.infer<typeof livePriceQuoteResponseSchema>;

export const liveQuoteFrameSchema = z.object({
  symbol: z.string(),
  market: z.string(),
  quote: z.object({
    source_id: z.string().optional(),
    currentness_caveat: z.string().optional(),
    health_status: z.string().optional(),
    tick: livePriceTickSchema.nullable().optional(),
    // A locked/masked frame (`MaskedTeaser`) never happens for this PUBLIC tool today, but the
    // shape is still open (`.optional()` on every field above) rather than `.strict()` — this
    // mirrors `dataUpdatedPayloadSchema`'s own "narrow only what we render" posture.
    locked: z.boolean().optional(),
  }),
  as_of: z.string().nullable().optional(),
  market_open: z.boolean(),
});
export type LiveQuoteFrame = z.infer<typeof liveQuoteFrameSchema>;
