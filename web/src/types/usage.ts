/**
 * LLM usage shapes - `GET /usage/today`, `GET /usage/llm`, and the per-turn `usage` SSE frame.
 * Mirrors `backend/src/api/routers/accounts_extra.py` and `backend/src/usage/llm_usage.py`.
 *
 * `cost_micros` is millionths of a USD and is only non-zero where the provider reports a charge
 * on the response itself (OpenRouter); it is never an estimate.
 */
import { z } from "zod";

export const todayUsageSchema = z.object({
  chats_used: z.number().int(),
});
export type TodayUsage = z.infer<typeof todayUsageSchema>;

const totalsShape = {
  calls: z.number().int(),
  input_tokens: z.number().int(),
  output_tokens: z.number().int(),
  cached_tokens: z.number().int(),
  cache_write_tokens: z.number().int().default(0),
  reasoning_tokens: z.number().int(),
  cost_micros: z.number().int(),
  estimated_cost_micros: z.number().int().default(0),
};

export const usageTotalsSchema = z.object(totalsShape);
export type UsageTotals = z.infer<typeof usageTotalsSchema>;

export const dailyUsageSchema = z.object({
  date: z.string(),
  by_provider: z.record(z.string(), z.number().int()).default({}),
  by_model: z.record(z.string(), z.number().int()).default({}),
  ...totalsShape,
});
export type DailyUsage = z.infer<typeof dailyUsageSchema>;

export const modelTotalsSchema = z.object({
  provider: z.string(),
  model: z.string(),
  priced: z.boolean().default(false),
  ...totalsShape,
});
export type ModelTotals = z.infer<typeof modelTotalsSchema>;

export const llmUsageSummarySchema = z.object({
  days: z.number().int(),
  providers: z.array(z.string()).default([]),
  daily: z.array(dailyUsageSchema),
  models: z.array(modelTotalsSchema),
  totals: usageTotalsSchema,
  byok_calls: z.number().int(),
});
export type LlmUsageSummary = z.infer<typeof llmUsageSummarySchema>;

/** A long-term memory that informed a turn (shown under the answer). */
export const memoryRefSchema = z.object({
  id: z.string(),
  key: z.string(),
  type: z.string(),
  text: z.string().default(""),
});
export type MemoryRef = z.infer<typeof memoryRefSchema>;

/** The `memory.used` SSE frame's `data`. */
export const memoryUsedDataSchema = z.object({ memories: z.array(memoryRefSchema).default([]) });

/** The `personality.used` SSE frame's `data`. */
export const personalityUsedDataSchema = z.object({ personality: z.string() });

/** The `pet.leveled` / `pet.pull_earned` SSE frames' `data`. */
export const petLeveledDataSchema = z.object({ pet_id: z.string(), level: z.number().int() });
export const petPullEarnedDataSchema = z.object({ count: z.number().int() });

/** The `usage` SSE frame's `data` - one turn's totals plus the per-model split. */
export const turnUsageDataSchema = z.object({
  input_tokens: z.number().int().default(0),
  output_tokens: z.number().int().default(0),
  cost_micros: z.number().int().default(0),
  // List-price estimate (BYOK turns only), apart from the provider-reported `cost_micros`.
  estimated_cost_micros: z.number().int().default(0),
  models: z
    .array(
      z.object({
        provider: z.string(),
        model: z.string(),
        calls: z.number().int().default(0),
        input_tokens: z.number().int().default(0),
        output_tokens: z.number().int().default(0),
        cached_tokens: z.number().int().default(0),
        reasoning_tokens: z.number().int().default(0),
        cost_micros: z.number().int().default(0),
      }),
    )
    .default([]),
  // Present on a stored turn (and only there): the memories that informed it.
  memories_used: z.array(memoryRefSchema).default([]),
  // Also stored-turn only: the 語氣 preset id that styled the reply.
  personality: z.string().nullable().default(null),
});
export type TurnUsageData = z.infer<typeof turnUsageDataSchema>;
