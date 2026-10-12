/**
 * 自選股 (watchlist) API client — mirrors `backend/src/api/routers/memory.py`'s `/watchlists/*`
 * routes exactly. Requests go through the shared `lib/api/http.ts` seam (credentials, refresh on
 * 401, the `{code, message, retryable}` envelope parsed into `WatchlistApiError`) and every
 * response is Zod-parsed at the boundary.
 */
import { ApiRequestError, createRequestJson, jsonHeaders } from "@/lib/api/http";
import type { Market } from "@/types/settings";
import {
  listWatchlistsResponseSchema,
  type WatchlistAssetClass,
  type WatchlistColor,
  type WatchlistGroup,
  type WatchlistGroupView,
  type WatchlistItem,
  watchlistGroupSchema,
  watchlistItemSchema,
} from "@/types/watchlist";

export class WatchlistApiError extends ApiRequestError {
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super("WatchlistApiError", code, message, status, retryable);
  }
}

const requestJson = createRequestJson(
  (code, message, status, retryable) => new WatchlistApiError(code, message, status, retryable),
);

/** `If-Match: <version>` is how the backend's optimistic `version` CAS is threaded through — a
 * stale write raises `WatchlistApiError` with `status === 409`, `retryable === true`. */
function versionHeaders(version: number): HeadersInit {
  return { "If-Match": String(version) };
}

// ---------------------------------------------------------------------------
// Groups
// ---------------------------------------------------------------------------

export async function getWatchlists(market?: Market): Promise<WatchlistGroupView[]> {
  const suffix = market ? `?market=${market}` : "";
  const raw = await requestJson(`/watchlists${suffix}`);
  return listWatchlistsResponseSchema.parse(raw).watchlists;
}

export async function createWatchlist(
  name: string,
  market: Market,
  assetClass: WatchlistAssetClass,
): Promise<WatchlistGroup> {
  const raw = await requestJson("/watchlists", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ name, market, asset_class: assetClass }),
  });
  return watchlistGroupSchema.parse(raw);
}

export async function renameWatchlist(
  watchlistId: string,
  name: string,
  version: number,
): Promise<WatchlistGroup> {
  const raw = await requestJson(`/watchlists/${encodeURIComponent(watchlistId)}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...versionHeaders(version) },
    body: JSON.stringify({ name }),
  });
  return watchlistGroupSchema.parse(raw);
}

/** Colours a group's label from the palette; `null` clears it (sent as the server's "none"). */
export async function setWatchlistColor(
  watchlistId: string,
  color: WatchlistColor | null,
  version: number,
): Promise<WatchlistGroup> {
  const raw = await requestJson(`/watchlists/${encodeURIComponent(watchlistId)}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...versionHeaders(version) },
    body: JSON.stringify({ color: color ?? "none" }),
  });
  return watchlistGroupSchema.parse(raw);
}

/** Moves a group between two neighbors — `afterId`/`beforeId` `null` means "no bound on that
 * side" (move to an end). Matches `PatchWatchlistRequest`'s own after_id/before_id pair. */
export async function reorderWatchlistGroup(
  watchlistId: string,
  afterId: string | null,
  beforeId: string | null,
  version: number,
): Promise<WatchlistGroup> {
  const raw = await requestJson(`/watchlists/${encodeURIComponent(watchlistId)}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...versionHeaders(version) },
    body: JSON.stringify({ after_id: afterId, before_id: beforeId }),
  });
  return watchlistGroupSchema.parse(raw);
}

export async function deleteWatchlist(watchlistId: string): Promise<void> {
  await requestJson(`/watchlists/${encodeURIComponent(watchlistId)}`, {
    method: "DELETE",
    expectBody: false,
  });
}

/** Idempotent star-add — a repeat add of the same symbol to the same group returns the existing
 * row, never a duplicate (backend's own `WatchlistStore.add_item` guarantee). */
export async function addWatchlistItem(
  watchlistId: string,
  symbol: string,
  note?: string | null,
): Promise<WatchlistItem> {
  const raw = await requestJson(
    `/watchlists/${encodeURIComponent(watchlistId)}/items/${encodeURIComponent(symbol)}`,
    {
      method: "PUT",
      headers: jsonHeaders(),
      body: JSON.stringify({ note: note ?? null }),
    },
  );
  return watchlistItemSchema.parse(raw);
}

/** Idempotent star-remove — 204 whether or not the symbol was starred in this group. */
export async function removeWatchlistItem(watchlistId: string, symbol: string): Promise<void> {
  await requestJson(
    `/watchlists/${encodeURIComponent(watchlistId)}/items/${encodeURIComponent(symbol)}`,
    { method: "DELETE", expectBody: false },
  );
}

/** Move an item to another of the caller's groups (drag-across-groups) — takes the target
 * group's own market/asset_class server-side; `afterId` places it after a specific item, or
 * trailing if omitted. */
export async function moveWatchlistItem(
  itemId: string,
  currentWatchlistId: string,
  targetWatchlistId: string,
  afterId?: string | null,
): Promise<WatchlistItem> {
  const raw = await requestJson(
    `/watchlists/${encodeURIComponent(currentWatchlistId)}/items/${encodeURIComponent(itemId)}/move`,
    {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({
        target_watchlist_id: targetWatchlistId,
        after_id: afterId ?? null,
      }),
    },
  );
  return watchlistItemSchema.parse(raw);
}
