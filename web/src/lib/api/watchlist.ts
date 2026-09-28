/**
 * 自選股 (watchlist) API client — mirrors `backend/src/api/routers/memory.py`'s `/watchlists/*`
 * routes exactly. Follows `lib/api/settings.ts`'s established convention: `fetchWithRefresh` +
 * `apiUrl` + Zod-parse-at-the-boundary + a typed error class + a dedicated `requestJson` (method/
 * body support, `expectBody:false` for 204s) — kept as this file's own copy rather than importing
 * `settings.ts`'s, since the two are meant to evolve independently per backend router area.
 */
import { fetchWithRefresh } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/config";
import { apiErrorSchema } from "@/lib/api/schemas";
import type { Market } from "@/types/settings";
import {
  listWatchlistItemsResponseSchema,
  listWatchlistsResponseSchema,
  type WatchlistAssetClass,
  type WatchlistGroup,
  type WatchlistGroupView,
  type WatchlistItem,
  watchlistGroupSchema,
  watchlistItemSchema,
} from "@/types/watchlist";

export class WatchlistApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super(message);
    this.name = "WatchlistApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

async function requestJson(
  path: string,
  init: RequestInit & { expectBody?: boolean } = {},
): Promise<unknown> {
  const { expectBody = true, ...rest } = init;
  const response = await fetchWithRefresh(apiUrl(path), {
    credentials: "include",
    headers: { accept: "application/json", ...rest.headers },
    ...rest,
  });
  if (!response.ok) {
    const parsed = apiErrorSchema.safeParse(await response.json().catch(() => null));
    if (parsed.success) {
      throw new WatchlistApiError(
        parsed.data.code,
        parsed.data.message,
        response.status,
        parsed.data.retryable,
      );
    }
    throw new WatchlistApiError(
      "unknown_error",
      `Request failed (${response.status})`,
      response.status,
      response.status >= 500,
    );
  }
  if (!expectBody || response.status === 204) {
    return null;
  }
  return response.json();
}

function jsonHeaders(): HeadersInit {
  return { "content-type": "application/json" };
}

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

export async function reorderWatchlists(orderedIds: readonly string[]): Promise<void> {
  await requestJson("/watchlists/reorder", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({ ordered_ids: orderedIds }),
    expectBody: false,
  });
}

// ---------------------------------------------------------------------------
// Items
// ---------------------------------------------------------------------------

export async function getWatchlistItems(watchlistId: string): Promise<WatchlistItem[]> {
  const raw = await requestJson(`/watchlists/${encodeURIComponent(watchlistId)}/items`);
  return listWatchlistItemsResponseSchema.parse(raw).items;
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

export async function setWatchlistItemNote(
  watchlistId: string,
  itemId: string,
  note: string | null,
): Promise<WatchlistItem> {
  const raw = await requestJson(
    `/watchlists/${encodeURIComponent(watchlistId)}/items/${encodeURIComponent(itemId)}`,
    {
      method: "PATCH",
      headers: jsonHeaders(),
      body: JSON.stringify(note === null ? { clear_note: true } : { note }),
    },
  );
  return watchlistItemSchema.parse(raw);
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
