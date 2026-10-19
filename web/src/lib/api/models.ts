/**
 * Model catalog client - `GET /models`, `GET|PUT /me/model-default`.
 *
 * Mirrors `backend/src/api/routers/models.py`. Credentialed; every response is Zod-validated at
 * the boundary. `PUT` with `null` clears the saved default; a caller with no own keys is refused
 * with a 403 `byok_required` envelope, which surfaces as `ModelsApiError.code`.
 */
import { ApiRequestError, createRequestJson, jsonHeaders } from "@/lib/api/http";
import {
  type ModelCatalog,
  type ModelDefault,
  modelCatalogSchema,
  modelDefaultSchema,
} from "@/types/models";

export class ModelsApiError extends ApiRequestError {
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super("ModelsApiError", code, message, status, retryable);
  }
}

const requestJson = createRequestJson(
  (code, message, status, retryable) => new ModelsApiError(code, message, status, retryable),
);

/** `GET /models` - what the caller may pick from, and whether they may pick at all. */
export async function getModelCatalog(signal?: AbortSignal): Promise<ModelCatalog> {
  return modelCatalogSchema.parse(await requestJson("/models", signal ? { signal } : {}));
}

/** `GET /me/model-default` - the saved default model (null when none). */
export async function getModelDefault(signal?: AbortSignal): Promise<ModelDefault> {
  return modelDefaultSchema.parse(await requestJson("/me/model-default", signal ? { signal } : {}));
}

/** `PUT /me/model-default` - save a default model, or `null` to clear it. BYOK only. */
export async function putModelDefault(defaultModel: string | null): Promise<ModelDefault> {
  const raw = await requestJson("/me/model-default", {
    method: "PUT",
    headers: { ...jsonHeaders(), accept: "application/json" },
    body: JSON.stringify({ default_model: defaultModel }),
  });
  return modelDefaultSchema.parse(raw);
}
