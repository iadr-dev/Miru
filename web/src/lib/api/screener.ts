/**
 * Screener API client — mirrors `backend/src/api/routers/analytics.py`'s `/analytics/screener/*`
 * routes exactly. Follows `lib/api/watchlist.ts`'s established convention: `fetchWithRefresh` +
 * `apiUrl` + Zod-parse-at-the-boundary + a typed error class + a dedicated `requestJson` (method/
 * body support, `expectBody:false` for 204s).
 */
import { fetchWithRefresh } from "@/lib/api/auth";
import { apiUrl } from "@/lib/api/config";
import { apiErrorSchema } from "@/lib/api/schemas";
import {
  type FactorCatalog,
  factorCatalogResponseSchema,
  type RunScreenerResult,
  runScreenerResponseSchema,
  type ScreenerGateOverrides,
  type ScreenerMarket,
  type ScreenerPreset,
  type ScreenerRunCondition,
  type ScreenerScreen,
  screenerPresetsResponseSchema,
  screenerScreenSchema,
  screenerScreensResponseSchema,
} from "@/types/screener";

export class ScreenerApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super(message);
    this.name = "ScreenerApiError";
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
      throw new ScreenerApiError(
        parsed.data.code,
        parsed.data.message,
        response.status,
        parsed.data.retryable,
      );
    }
    throw new ScreenerApiError(
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

// ---------------------------------------------------------------------------
// GET /analytics/screener/factors
// ---------------------------------------------------------------------------

export async function getFactorCatalog(market: ScreenerMarket): Promise<FactorCatalog> {
  const raw = await requestJson(`/analytics/screener/factors?market=${market}`);
  return factorCatalogResponseSchema.parse(raw).catalog;
}

// ---------------------------------------------------------------------------
// GET /analytics/screener/presets
// ---------------------------------------------------------------------------

export async function getScreenerPresets(market: ScreenerMarket): Promise<ScreenerPreset[]> {
  const raw = await requestJson(`/analytics/screener/presets?market=${market}`);
  return screenerPresetsResponseSchema.parse(raw).presets;
}

// ---------------------------------------------------------------------------
// POST /analytics/screener/run
// ---------------------------------------------------------------------------

export interface RunScreenerParams {
  market: ScreenerMarket;
  conditions: ScreenerRunCondition[];
  match?: "AND" | "OR";
  gateOverrides?: ScreenerGateOverrides;
  countOnly?: boolean;
  size?: number;
}

export async function runScreener(params: RunScreenerParams): Promise<RunScreenerResult> {
  const raw = await requestJson("/analytics/screener/run", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({
      market: params.market,
      conditions: params.conditions,
      match: params.match ?? "AND",
      gate_overrides: params.gateOverrides ?? null,
      count_only: params.countOnly ?? false,
      size: params.size ?? 50,
    }),
  });
  return runScreenerResponseSchema.parse(raw).result;
}

// ---------------------------------------------------------------------------
// /analytics/screener/screens — save/list/update/delete
// ---------------------------------------------------------------------------

export async function listScreenerScreens(market: ScreenerMarket): Promise<ScreenerScreen[]> {
  const raw = await requestJson(`/analytics/screener/screens?market=${market}`);
  return screenerScreensResponseSchema.parse(raw).screens;
}

export interface SaveScreenerScreenParams {
  name: string;
  market: ScreenerMarket;
  conditions: ScreenerRunCondition[];
  match?: "AND" | "OR";
  gateOverrides?: ScreenerGateOverrides;
}

export async function saveScreenerScreen(
  params: SaveScreenerScreenParams,
): Promise<ScreenerScreen> {
  const raw = await requestJson("/analytics/screener/screens", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify({
      name: params.name,
      market: params.market,
      conditions: params.conditions,
      match: params.match ?? "AND",
      gate_overrides: params.gateOverrides ?? null,
    }),
  });
  return screenerScreenSchema.parse(raw);
}

export interface UpdateScreenerScreenParams {
  name?: string;
  conditions?: ScreenerRunCondition[];
  match?: "AND" | "OR";
  gateOverrides?: ScreenerGateOverrides;
}

export async function updateScreenerScreen(
  screenId: string,
  params: UpdateScreenerScreenParams,
): Promise<ScreenerScreen> {
  const raw = await requestJson(`/analytics/screener/screens/${encodeURIComponent(screenId)}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify({
      name: params.name,
      conditions: params.conditions,
      match: params.match,
      gate_overrides: params.gateOverrides,
    }),
  });
  return screenerScreenSchema.parse(raw);
}

export async function deleteScreenerScreen(screenId: string): Promise<void> {
  await requestJson(`/analytics/screener/screens/${encodeURIComponent(screenId)}`, {
    method: "DELETE",
    expectBody: false,
  });
}
