/**
 * Account + entitlement contracts surfaced by `GET /me`.
 *
 * Mirrors `backend/src/accounts/models.py` (`User`, `EntitlementView`, `Tier` from
 * `schemas.enums`) and `backend/src/api/routers/auth.py` (`MeResponse = {user, entitlements}`).
 * This is the shared, reusable shape (the shell top bar reads the tier + account; other features
 * will read the same `/me`, and `PATCH /me` answers with this envelope too).
 */
import { z } from "zod";

/** `schemas.enums.Tier`. */
export const TIERS = ["free", "premium", "pro"] as const;
export type Tier = (typeof TIERS)[number];

export const userSchema = z.object({
  id: z.string(),
  email: z.string().nullable().optional(),
  display_name: z.string().default(""),
  picture_url: z.string().nullable().optional(),
  birthday: z.string().nullable().optional(),
  phone_country_code: z.string().nullable().optional(),
  phone_number: z.string().nullable().optional(),
  nationality: z.string().nullable().optional(),
  tier: z.enum(TIERS).default("free"),
  personality_id: z.string().default("steady"),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Account = z.infer<typeof userSchema>;

/** `EntitlementView` — the resolved capability envelope. `chat_limit_per_day: null` = unlimited. */
export const entitlementViewSchema = z.object({
  tier: z.enum(TIERS),
  chat_limit_per_day: z.number().int().nullable(),
  requests_per_minute: z.number().int(),
  finmind_class: z.enum(TIERS),
  byok_active: z.boolean(),
  features: z.array(z.string()).default([]),
  // Models pickable on the shared key; null = unrestricted (paid plan, or own keys).
  platform_models: z.array(z.string()).nullable().default(null),
});
export type EntitlementView = z.infer<typeof entitlementViewSchema>;

/** `GET /me` → `MeResponse`. Extra `user` fields the shell doesn't need are dropped by the schema;
 * the ones it does need (id, display_name, email, picture_url, tier) are validated. */
export const meResponseSchema = z.object({
  user: userSchema,
  entitlements: entitlementViewSchema,
});
export type MeResponse = z.infer<typeof meResponseSchema>;
