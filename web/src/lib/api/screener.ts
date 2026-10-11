/**
 * Screener API client — mirrors `backend/src/api/routers/analytics.py`'s `/analytics/screener/*`
 * routes exactly. Requests go through the shared `lib/api/http.ts` seam (credentials, refresh on
 * 401, the `{code, message, retryable}` envelope parsed into `ScreenerApiError`) and every response
 * is Zod-parsed at the boundary.
 */
import { ApiRequestError, createRequestJson, jsonHeaders } from "@/lib/api/http";
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

export class ScreenerApiError extends ApiRequestError {
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super("ScreenerApiError", code, message, status, retryable);
  }
}

const requestJson = createRequestJson(
  (code, message, status, retryable) => new ScreenerApiError(code, message, status, retryable),
);

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
