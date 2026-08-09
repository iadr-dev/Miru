/**
 * Zod schemas mirroring the backend contracts we touch on the entry/auth pass.
 * Mirrors `backend/src/api/routers/auth.py` (BeginAuthResponse, MeResponse) and
 * `backend/src/api/errors.py` (the `{code, message, retryable}` envelope). Parse-don't-trust:
 * a malformed payload fails loud at the seam (web/CLAUDE.md §2).
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

/** Minimal shape of GET /me / callback MeResponse — only what the landing redirect needs. */
export const meResponseSchema = z.object({
  user: z.object({
    id: z.string(),
    display_name: z.string().nullable().optional(),
    email: z.string().nullable().optional(),
  }),
  entitlements: z
    .object({
      tier: z.string().optional(),
    })
    .passthrough()
    .optional(),
});
export type MeResponse = z.infer<typeof meResponseSchema>;
