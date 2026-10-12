/**
 * Zod schemas mirroring the backend contracts we touch on the entry/auth pass.
 * Mirrors `backend/src/api/routers/auth.py` (BeginAuthResponse) and `backend/src/api/errors.py`
 * (the `{code, message, retryable}` envelope). Parse-don't-trust: a malformed payload fails loud
 * at the seam (web/OVERVIEW.md §2). The callback endpoint's own `MeResponse` schema lives in
 * `@/types/account` (shared with `lib/api/settings.ts`'s `GET /me`) — not duplicated here.
 */
import { z } from "zod";

/** POST /auth/oauth/{provider} → BeginAuthResponse. */
export const beginAuthResponseSchema = z.object({
  authorization_url: z.string().url(),
  retry_authorization_url: z.string().url().nullable().optional(),
});
export type BeginAuthResponse = z.infer<typeof beginAuthResponseSchema>;

/** The canonical error envelope every router raises via ApiError. */
export const apiErrorSchema = z.object({
  code: z.string(),
  message: z.string(),
  retryable: z.boolean().optional().default(false),
});
export type ApiErrorPayload = z.infer<typeof apiErrorSchema>;
