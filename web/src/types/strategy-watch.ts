/**
 * 策略盯盤 (strategy watch) — `/strategy-watch/today` (the `get_strategy_watch` tool) and the
 * per-user subscription at `/strategy-watch/subscription`. Mirrors
 * `backend/src/tools/market/strategy_watch.py` and `backend/src/scheduler/models.py`.
 */

import { z } from "zod";
import { DELIVERY_CHANNEL_KINDS } from "@/types/settings";

export const STRATEGY_KEYS = [
  "volatility_squeeze",
  "oversold_bounce",
  "ad_divergence",
  "chip_accumulation",
  "momentum_flag",
] as const;

export const strategyKeySchema = z.enum(STRATEGY_KEYS);
export type StrategyKey = z.infer<typeof strategyKeySchema>;

export const strategyConditionSchema = z.object({
  key: z.string(),
  kind: z.enum(["trigger", "guard"]),
  met: z.boolean(),
  observed: z.number().nullable(),
  required: z.number().nullable(),
});

export type StrategyCondition = z.infer<typeof strategyConditionSchema>;

const level = z.number().nullable();

export const strategyWatchItemSchema = z.object({
  symbol: z.string(),
  name: z.string().nullable(),
  strategy: strategyKeySchema,
  industry: z.string().nullable(),
  close: z.number(),
  upper_band: level,
  lower_band: level,
  low_20d: level,
  range_high_20d: level,
  range_low_20d: level,
  flag_high: level,
  flag_low: level,
  sector_strength_pct: level,
  foreign_streak: z.number(),
  trust_streak: z.number(),
  large_holder_pct: level,
  large_holder_change_4w: level,
  holders_change_4w_pct: level,
  margin_change_20d_pct: level,
  headline: z.string().nullable(),
  days_on_list: z.number(),
  first_listed: z.string().nullable(),
  latest_slot: z.string().nullable(),
  price: level,
  conditions: z.array(strategyConditionSchema).default([]),
  all_met: z.boolean(),
  confirmed: z.boolean(),
  alerted_at: z.string().nullable(),
  close_confirmed: z.boolean(),
});

export type StrategyWatchItem = z.infer<typeof strategyWatchItemSchema>;

export const strategyWatchEventSchema = z.object({
  slot: z.string(),
  symbol: z.string(),
  name: z.string().nullable(),
  strategy: strategyKeySchema,
  kind: z.enum(["intraday", "close"]),
});

export type StrategyWatchEvent = z.infer<typeof strategyWatchEventSchema>;

/** The latest session each post-close refresh has real rows for (股價, 融資, 三大法人). */
export const strategyWatchDataDatesSchema = z.object({
  price_eod: z.string().nullable(),
  margin_short: z.string().nullable(),
  institutional_flow: z.string().nullable(),
});

export type StrategyWatchDataDates = z.infer<typeof strategyWatchDataDatesSchema>;

/** One intraday check that ran (the 10-minute slots the timeline ticks). */
export const strategyWatchSlotSchema = z.object({
  slot: z.string(),
  checked: z.number(),
  all_met: z.number(),
  alertable: z.number(),
});

export type StrategyWatchSlot = z.infer<typeof strategyWatchSlotSchema>;

export const strategyWatchTodaySchema = z.object({
  data_available: z.boolean(),
  unavailable_reason: z.string().nullable(),
  run_date: z.string().nullable(),
  as_of: z.string().nullable(),
  regime: z.enum(["risk_on", "neutral", "risk_off"]).nullable(),
  breadth_pct: z.number().nullable(),
  last_slot: z.string().nullable(),
  counts: z.record(z.string(), z.number()).default({}),
  items: z.array(strategyWatchItemSchema).default([]),
  events: z.array(strategyWatchEventSchema).default([]),
  masked: z.boolean(),
  data_dates: strategyWatchDataDatesSchema,
  slots: z.array(strategyWatchSlotSchema).default([]),
});

export type StrategyWatchToday = z.infer<typeof strategyWatchTodaySchema>;

export const quietHoursSchema = z.object({ start: z.string(), end: z.string() });

export const strategySubscriptionSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  strategies: z.array(strategyKeySchema),
  watchlist_id: z.string().nullable(),
  morning_list: z.boolean(),
  channels: z.array(z.enum(DELIVERY_CHANNEL_KINDS)),
  quiet_hours: quietHoursSchema.nullable(),
  daily_limit: z.number().nullable(),
  enabled: z.boolean(),
  version: z.number(),
  created_at: z.string(),
  updated_at: z.string(),
});

export type StrategySubscription = z.infer<typeof strategySubscriptionSchema>;

/** `GET /strategy-watch/subscription` returns the subscription or `null` before the first save. */
export const strategySubscriptionOrNullSchema = strategySubscriptionSchema.nullable();

export type StrategySubscriptionInput = {
  strategies: StrategyKey[];
  watchlist_id: string | null;
  morning_list: boolean;
  daily_limit: number | null;
  enabled: boolean;
  expected_version?: number;
};
