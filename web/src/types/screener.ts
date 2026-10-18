/**
 * Screener contracts — mirrors `backend/src/api/routers/analytics.py`'s `/analytics/screener/*`
 * routes and `backend/src/analytics/screener/{factor_catalog,presets,engine}.py`. `ScreenerMarket`
 * is the screener's OWN three-tab model (`"TW"|"US"|"ETF"`) — distinct from `types/settings.ts`'s
 * `Market` (`TW|US|GB|JP`), since a TW-ETF is `(Market.TW, etf)` everywhere else in the app but a
 * peer top-level tab here.
 */
import { z } from "zod";

export const SCREENER_MARKETS = ["TW", "US", "ETF"] as const;
export const screenerMarketSchema = z.enum(SCREENER_MARKETS);
export type ScreenerMarket = z.infer<typeof screenerMarketSchema>;

export const SCREENER_OPERATORS = ["gt", "gte", "lt", "lte", "between", "eq", "in"] as const;
export const screenerOperatorSchema = z.enum(SCREENER_OPERATORS);
export type ScreenerOperator = z.infer<typeof screenerOperatorSchema>;

export const SCREENER_DATA_CLASSES = ["public", "sponsor", "private"] as const;
export const screenerDataClassSchema = z.enum(SCREENER_DATA_CLASSES);
export type ScreenerDataClass = z.infer<typeof screenerDataClassSchema>;

// ---------------------------------------------------------------------------
// GET /analytics/screener/factors — get_factor_catalog
// ---------------------------------------------------------------------------

export const factorFieldSchema = z.object({
  key: z.string(),
  label_zh: z.string(),
  label_en: z.string(),
  family: z.string(),
  unit: z.string().nullable(),
  min: z.number().nullable(),
  max: z.number().nullable(),
  step: z.number().nullable(),
  preset_chips: z.array(z.number()).default([]),
  enum_values: z.array(z.string()).nullable(),
  op_set: z.array(screenerOperatorSchema),
  data_class: screenerDataClassSchema,
  source_field: z.string(),
});
export type FactorField = z.infer<typeof factorFieldSchema>;

export const signalTagSchema = z.object({
  key: z.string(),
  label_zh: z.string(),
  label_en: z.string(),
  family: z.string(),
  description: z.string(),
  data_class: screenerDataClassSchema,
});
export type SignalTag = z.infer<typeof signalTagSchema>;

export const factorCatalogSchema = z.object({
  market: z.string(),
  signals: z.array(signalTagSchema).default([]),
  fields: z.array(factorFieldSchema).default([]),
  families: z.array(z.string()).default([]),
});
export type FactorCatalog = z.infer<typeof factorCatalogSchema>;

export const factorCatalogResponseSchema = z.object({ catalog: factorCatalogSchema });
export type FactorCatalogResponse = z.infer<typeof factorCatalogResponseSchema>;

// ---------------------------------------------------------------------------
// GET /analytics/screener/presets — get_screener_presets
// ---------------------------------------------------------------------------

export const presetConditionSchema = z.object({
  factor: z.string(),
  op: screenerOperatorSchema,
  value: z.union([z.number(), z.string()]).nullable(),
  value2: z.union([z.number(), z.string()]).nullable(),
});
export type PresetCondition = z.infer<typeof presetConditionSchema>;

export const screenerPresetSchema = z.object({
  key: z.string(),
  category: z.string(),
  label_zh: z.string(),
  label_en: z.string(),
  description: z.string(),
  direction: z.enum(["bull", "bear", "neutral"]),
  conditions: z.array(presetConditionSchema),
  match: z.enum(["AND", "OR"]),
});
export type ScreenerPreset = z.infer<typeof screenerPresetSchema>;

export const screenerPresetsResponseSchema = z.object({
  presets: z.array(screenerPresetSchema).default([]),
});
export type ScreenerPresetsResponse = z.infer<typeof screenerPresetsResponseSchema>;

// ---------------------------------------------------------------------------
// POST /analytics/screener/run — run_screener
// ---------------------------------------------------------------------------

export const screenerRunConditionSchema = z.object({
  factor: z.string(),
  op: screenerOperatorSchema,
  value: z.union([z.number(), z.string()]).optional(),
  value2: z.union([z.number(), z.string()]).optional(),
});
export type ScreenerRunCondition = z.infer<typeof screenerRunConditionSchema>;

export const screenerGateOverridesSchema = z.object({
  min_price: z.number().optional(),
  min_avg_volume: z.number().optional(),
  min_market_cap: z.number().optional(),
  exclude_disposition: z.boolean().optional(),
  exclude_attention: z.boolean().optional(),
  exclude_full_cash_delivery: z.boolean().optional(),
  honeymoon_exclude_days: z.number().optional(),
  exclude_leveraged_inverse: z.boolean().optional(),
  min_aum: z.number().optional(),
});
export type ScreenerGateOverrides = z.infer<typeof screenerGateOverridesSchema>;

export const screenerSurvivorSchema = z.object({
  symbol: z.string(),
  matched_conditions: z.array(z.string()).default([]),
  /** US-only: a projection of quote fields Yahoo's screener response already returned for this
   * hit (price/change%/volume/market cap/sector/PE/institutional%/short-float%) — empty for
   * TW/ETF, which get those fields from `useSymbolQuotes` instead. Mirrors
   * `backend/src/analytics/screener/engine.py`'s `ScreenerSurvivor.display`. */
  display: z
    .record(z.string(), z.union([z.number(), z.string(), z.boolean()]).nullable())
    .default({}),
});
export type ScreenerSurvivor = z.infer<typeof screenerSurvivorSchema>;

export const funnelCountsSchema = z.object({
  universe: z.number(),
  gated: z.number(),
  setup: z.number(),
});
export type FunnelCounts = z.infer<typeof funnelCountsSchema>;

/** US reports `universe=0` as a sentinel — yfinance's screener has no separate whole-market
 * pre-filter stage to count (`backend/src/analytics/screener/engine.py`'s `run_screener`), not a
 * literal empty market. `setup` is always a subset of a real `universe` count, so `setup >
 * universe` is the tell that the number is the "unknown" sentinel rather than a real zero. */
export function isFunnelUniverseKnown(funnel: FunnelCounts | undefined): boolean {
  if (!funnel) return true;
  return funnel.setup <= funnel.universe;
}

export const runScreenerResultSchema = z.object({
  market: z.string(),
  funnel: funnelCountsSchema,
  survivors: z.array(screenerSurvivorSchema).default([]),
  detail: z.string(),
  degraded: z.array(z.string()).default([]),
});
export type RunScreenerResult = z.infer<typeof runScreenerResultSchema>;

export const runScreenerResponseSchema = z.object({ result: runScreenerResultSchema });
export type RunScreenerResponse = z.infer<typeof runScreenerResponseSchema>;

// ---------------------------------------------------------------------------
// /analytics/screener/screens — save/list/update/delete (memory/screener_screens.py)
// ---------------------------------------------------------------------------

export const screenerScreenSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  name: z.string(),
  market: z.string(),
  conditions: z.array(z.record(z.string(), z.unknown())).default([]),
  match: z.enum(["AND", "OR"]),
  gate_overrides: z.record(z.string(), z.unknown()).nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ScreenerScreen = z.infer<typeof screenerScreenSchema>;

export const screenerScreensResponseSchema = z.object({
  screens: z.array(screenerScreenSchema).default([]),
});
export type ScreenerScreensResponse = z.infer<typeof screenerScreensResponseSchema>;
