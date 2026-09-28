/**
 * 自選股 (watchlist) — mirrors `backend/src/memory/watchlists.py`'s `WatchlistGroup`/
 * `WatchlistItem` pydantic models field-for-field, and `backend/src/api/routers/memory.py`'s
 * `WatchlistGroupView` response shape. Backend module: `memory.watchlists`. REST:
 * `GET/POST /watchlists`, `PATCH/DELETE /watchlists/{id}`, `PUT/DELETE
 * /watchlists/{id}/items/{symbol}`, `.../items/{id}/move` (see `lib/api/watchlist.ts`).
 */
import { z } from "zod";
import { MARKETS } from "@/types/settings";

export const WATCHLIST_ASSET_CLASSES = ["stock", "etf"] as const;
export const watchlistAssetClassSchema = z.enum(WATCHLIST_ASSET_CLASSES);
export type WatchlistAssetClass = z.infer<typeof watchlistAssetClassSchema>;

export const watchlistMarketSchema = z.enum(MARKETS);

export const watchlistGroupSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  name: z.string(),
  market: watchlistMarketSchema,
  asset_class: watchlistAssetClassSchema,
  sort_order: z.string(),
  is_default: z.boolean(),
  version: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type WatchlistGroup = z.infer<typeof watchlistGroupSchema>;

export const watchlistItemSchema = z.object({
  id: z.string(),
  watchlist_id: z.string(),
  user_id: z.string(),
  symbol: z.string(),
  market: watchlistMarketSchema,
  asset_class: watchlistAssetClassSchema,
  note: z.string().nullable(),
  sort_order: z.string(),
  added_at: z.string(),
});
export type WatchlistItem = z.infer<typeof watchlistItemSchema>;

// `GET /watchlists`' own response shape — `WatchlistGroupView` in memory.py.
export const watchlistGroupViewSchema = z.object({
  group: watchlistGroupSchema,
  items: z.array(watchlistItemSchema).default([]),
});
export type WatchlistGroupView = z.infer<typeof watchlistGroupViewSchema>;

export const listWatchlistsResponseSchema = z.object({
  watchlists: z.array(watchlistGroupViewSchema).default([]),
});

export const listWatchlistItemsResponseSchema = z.object({
  items: z.array(watchlistItemSchema).default([]),
});
