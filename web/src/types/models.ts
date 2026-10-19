/**
 * Model catalog + saved default model contracts.
 *
 * Mirrors `backend/src/api/routers/models.py`: `GET /models` -> `ModelCatalog`, and
 * `GET|PUT /me/model-default` -> `ModelDefault`. The catalog is only selectable for a caller with
 * their own keys (`selectable`); everyone else is served the platform's own choice.
 */
import { z } from "zod";

/** The providers the catalog can carry (same set the BYOK key picker offers). */
export const MODEL_PROVIDERS = ["anthropic", "openai", "gemini", "openrouter"] as const;

export const modelOptionSchema = z.object({
  provider: z.enum(MODEL_PROVIDERS),
  id: z.string(),
  label_zh: z.string(),
  label_en: z.string(),
  is_default: z.boolean(),
});
export type ModelOption = z.infer<typeof modelOptionSchema>;

export const modelCatalogSchema = z.object({
  selectable: z.boolean(),
  models: z.array(modelOptionSchema),
});
export type ModelCatalog = z.infer<typeof modelCatalogSchema>;

export const modelDefaultSchema = z.object({
  default_model: z.string().nullable(),
});
export type ModelDefault = z.infer<typeof modelDefaultSchema>;
