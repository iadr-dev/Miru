/**
 * Settings-domain contracts: personality presets, pets, billing, BYOK keys, usage quota,
 * schedules, alerts, notifications, alert-preferences, devices, memory.
 *
 * Mirrors `backend/src/personality/models.py` (`PersonalityPreset`), `backend/src/pets/models.py`
 * (`OwnedPet`/`PetStats`/`PetProgress`/`PullOutcome`), `backend/src/payments/models.py`
 * (`SubscriptionRecord`) + `payments/plans.py` (`PlanInfo`), `backend/src/scheduler/models.py`
 * (`ScheduleJob`/`AlertRule`/`Notification`/`NotificationBadge`/`AlertPreference`/
 * `DevicePushToken`), and `backend/src/memory/models.py` (`Memory`). Every response envelope
 * mirrors the corresponding router in `backend/src/api/routers/` (`accounts_extra.py`,
 * `pets.py`, `billing.py`, `scheduler.py`, `memory.py`). Parse-don't-trust at the seam.
 */
import { z } from "zod";
import { entitlementViewSchema, TIERS } from "@/types/account";

// ---------------------------------------------------------------------------
// Connections — `OAuthAccount` from `backend/src/accounts/models.py`, surfaced via
// `GET /me/connections` (`backend/src/api/routers/auth.py`).
// ---------------------------------------------------------------------------

export const OAUTH_LINK_PROVIDERS = ["google", "line"] as const;

export const oauthAccountSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  provider: z.enum(OAUTH_LINK_PROVIDERS),
  provider_account_id: z.string(),
  email_at_provider: z.string().nullable().optional(),
  linked_at: z.string(),
});
export type OAuthAccountLike = z.infer<typeof oauthAccountSchema>;

// ---------------------------------------------------------------------------
// Personality presets
// ---------------------------------------------------------------------------

export const personalityPresetSchema = z.object({
  id: z.string(),
  name_zh: z.string(),
  name_en: z.string(),
  description: z.string(),
  default: z.boolean().default(false),
  version: z.string().default("1.0.0"),
});
export type PersonalityPreset = z.infer<typeof personalityPresetSchema>;

export const personalityPresetsResponseSchema = z.object({
  presets: z.array(personalityPresetSchema).default([]),
});

// ---------------------------------------------------------------------------
// Pets
// ---------------------------------------------------------------------------

export const PET_RARITIES = ["common", "uncommon", "rare", "legendary"] as const;
export type PetRarity = (typeof PET_RARITIES)[number];

export const petCatalogEntrySchema = z.object({
  pet_id: z.string(),
  name: z.string(),
  name_zh: z.string(),
  rarity: z.enum(PET_RARITIES),
  description: z.string(),
});
export type PetCatalogEntry = z.infer<typeof petCatalogEntrySchema>;

export const ownedPetSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  pet_id: z.string(),
  is_active: z.boolean(),
  level: z.number().int(),
  xp: z.number().int(),
  obtained_via: z.enum(["pull", "admin"]),
  created_at: z.string(),
  updated_at: z.string(),
});
export type OwnedPet = z.infer<typeof ownedPetSchema>;

export const petProgressSchema = z.object({
  user_id: z.string(),
  total_pulls: z.number().int(),
  streak_days: z.number().int(),
  last_login_date: z.string().nullable(),
  chat_count: z.number().int(),
  available_pulls: z.number().int(),
  pity_counter: z.number().int(),
  catalog_size: z.number().int(),
  owned_count: z.number().int(),
  chats_per_bonus_pull: z.number().int(),
  streak_days_per_bonus_pull: z.number().int(),
  pity_rare_at: z.number().int(),
  pity_legendary_at: z.number().int(),
  level_milestones: z.record(z.string(), z.number().int()).default({}),
});
export type PetProgress = z.infer<typeof petProgressSchema>;

export const pullOutcomeSchema = z.object({
  pet_id: z.string(),
  rarity: z.enum(PET_RARITIES),
  is_duplicate: z.boolean(),
  xp_awarded: z.number().int().default(0),
  auto_equipped: z.boolean().default(false),
});
export type PullOutcome = z.infer<typeof pullOutcomeSchema>;

// ---------------------------------------------------------------------------
// Billing
// ---------------------------------------------------------------------------

export const PAYABLE_TIERS = ["premium", "pro"] as const;
export const BILLING_INTERVALS = ["monthly", "annual"] as const;

export const planInfoSchema = z.object({
  price_id: z.string(),
  tier: z.enum(PAYABLE_TIERS),
  interval: z.enum(BILLING_INTERVALS),
  byok: z.boolean(),
});
export type PlanInfo = z.infer<typeof planInfoSchema>;

export const plansResponseSchema = z.object({
  client_token: z.string().nullable(),
  plans: z.array(planInfoSchema).default([]),
});
export type PlansResponse = z.infer<typeof plansResponseSchema>;

export const checkoutResponseSchema = z.object({
  transaction_id: z.string(),
  checkout_url: z.string().nullable(),
});
export type CheckoutResponse = z.infer<typeof checkoutResponseSchema>;

export const subscriptionRecordSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  paddle_subscription_id: z.string(),
  paddle_customer_id: z.string().nullable(),
  status: z.string(),
  price_id: z.string(),
  tier: z.enum(PAYABLE_TIERS),
  byok: z.boolean(),
  current_period_start: z.string().nullable(),
  current_period_end: z.string().nullable(),
  scheduled_cancel_at: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type SubscriptionRecord = z.infer<typeof subscriptionRecordSchema>;

export const subscriptionResponseSchema = z.object({
  subscription: subscriptionRecordSchema.nullable(),
  byok_active: z.boolean(),
});
export type SubscriptionResponse = z.infer<typeof subscriptionResponseSchema>;

export const portalResponseSchema = z.object({
  portal_url: z.string().nullable(),
});
export type PortalResponse = z.infer<typeof portalResponseSchema>;

export const billingHistoryResponseSchema = z.object({
  transactions: z.array(z.record(z.string(), z.unknown())).default([]),
});
export type BillingTransaction = z.infer<
  typeof billingHistoryResponseSchema
>["transactions"][number];

// ---------------------------------------------------------------------------
// BYOK keys
// ---------------------------------------------------------------------------

export const SECRET_KINDS = [
  "llm_key",
  "finmind_token",
  "channel_token",
  "asr_key",
  "tts_key",
] as const;
export type SecretKind = (typeof SECRET_KINDS)[number];

/** The LLM providers this backend actually has an adapter + `*_API_KEY` setting for
 * (`backend/src/config/settings.py`) — the only providers a BYOK `llm_key` can mean anything
 * for. Free-text provider ids for other `SecretKind`s aren't offered here; this list is
 * specifically the LLM-key picker's option set. */
export const LLM_PROVIDERS = [
  "anthropic",
  "openai",
  "gemini",
  "groq",
  "cerebras",
  "openrouter",
] as const;
export type LlmProvider = (typeof LLM_PROVIDERS)[number];

// ---------------------------------------------------------------------------
// Usage quota — re-exports the shared entitlement envelope (limits only, no live consumption).
// ---------------------------------------------------------------------------

export { entitlementViewSchema as usageQuotaSchema };

// ---------------------------------------------------------------------------
// Scheduler shared shapes
// ---------------------------------------------------------------------------

export const MARKETS = ["TW", "US", "GB", "JP"] as const;
export type Market = (typeof MARKETS)[number];

export const DELIVERY_CHANNEL_KINDS = ["in_app", "line", "telegram", "email", "push"] as const;
export type DeliveryChannelKind = (typeof DELIVERY_CHANNEL_KINDS)[number];

export const quietHoursSchema = z.object({
  start: z.string().regex(/^\d{2}:\d{2}$/),
  end: z.string().regex(/^\d{2}:\d{2}$/),
});
export type QuietHours = z.infer<typeof quietHoursSchema>;

// ---------------------------------------------------------------------------
// Schedules (Pro-gated)
// ---------------------------------------------------------------------------

export const SCHEDULE_TRIGGER_TYPES = ["cron", "market_event"] as const;

export const scheduleTriggerSchema = z.object({
  type: z.enum(SCHEDULE_TRIGGER_TYPES),
  spec: z.string().min(1),
  market: z.enum(MARKETS).default("TW"),
});
export type ScheduleTrigger = z.infer<typeof scheduleTriggerSchema>;

export const scheduleActionSchema = z.object({
  skill_ref: z.string().min(1),
  inputs: z.record(z.string(), z.unknown()).default({}),
});
export type ScheduleAction = z.infer<typeof scheduleActionSchema>;

export const SCHEDULE_STATUSES = ["ok", "running", "skipped_quota", "error"] as const;

export const scheduleJobSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  trigger: scheduleTriggerSchema,
  action: scheduleActionSchema,
  delivery_channels: z.array(z.enum(DELIVERY_CHANNEL_KINDS)).default([]),
  quiet_hours: quietHoursSchema.nullable(),
  daily_limit: z.number().int().nullable(),
  enabled: z.boolean().default(true),
  last_run: z.string().nullable(),
  next_run: z.string().nullable(),
  status: z.enum(SCHEDULE_STATUSES).default("ok"),
  version: z.number().int().default(0),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ScheduleJob = z.infer<typeof scheduleJobSchema>;

export const schedulesResponseSchema = z.object({
  schedules: z.array(scheduleJobSchema).default([]),
});

// ---------------------------------------------------------------------------
// Alerts (Pro-gated)
// ---------------------------------------------------------------------------

export const ALERT_CONDITION_TYPES = ["price", "volume", "indicator", "institutional"] as const;
export type AlertConditionType = (typeof ALERT_CONDITION_TYPES)[number];

export const ALERT_COMPARATORS = [
  "above",
  "below",
  "crosses_above",
  "crosses_below",
  "streak_up",
  "streak_down",
] as const;
export type AlertComparator = (typeof ALERT_COMPARATORS)[number];

export const alertConditionSchema = z.object({
  type: z.enum(ALERT_CONDITION_TYPES),
  metric: z.string().min(1),
  comparator: z.enum(ALERT_COMPARATORS),
  threshold: z.number().int(),
  window: z.number().int().default(1),
});
export type AlertCondition = z.infer<typeof alertConditionSchema>;

export const alertRuleSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  symbol: z.string(),
  market: z.enum(MARKETS),
  condition: alertConditionSchema,
  channels: z.array(z.enum(DELIVERY_CHANNEL_KINDS)).default([]),
  quiet_hours: quietHoursSchema.nullable(),
  daily_limit: z.number().int().nullable(),
  enabled: z.boolean().default(true),
  last_triggered: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type AlertRule = z.infer<typeof alertRuleSchema>;

export const alertsResponseSchema = z.object({
  alerts: z.array(alertRuleSchema).default([]),
});

// ---------------------------------------------------------------------------
// Notifications (not tier-gated)
// ---------------------------------------------------------------------------

export const NOTIFICATION_SOURCES = ["schedule", "alert", "digest"] as const;

export const notificationSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  source: z.enum(NOTIFICATION_SOURCES),
  source_id: z.string(),
  channel: z.enum(DELIVERY_CHANNEL_KINDS),
  dedupe_key: z.string(),
  payload: z.record(z.string(), z.unknown()).default({}),
  delivered_at: z.string(),
  read_at: z.string().nullable(),
});
export type Notification = z.infer<typeof notificationSchema>;

export const notificationsResponseSchema = z.object({
  notifications: z.array(notificationSchema).default([]),
});

export const notificationBadgeSchema = z.object({
  unread_notifications: z.number().int().default(0),
  unread_threads: z.number().int().default(0),
  pending_alerts: z.number().int().default(0),
});
export type NotificationBadge = z.infer<typeof notificationBadgeSchema>;

// ---------------------------------------------------------------------------
// Alert preferences + devices
// ---------------------------------------------------------------------------

export const alertPreferenceSchema = z.object({
  user_id: z.string(),
  channels: z.array(z.enum(DELIVERY_CHANNEL_KINDS)).default([]),
  enabled_categories: z.array(z.string()).default([]),
  quiet_hours: quietHoursSchema.nullable(),
  daily_limit: z.number().int().nullable(),
  morning_digest: z.boolean().default(false),
  updated_at: z.string(),
});
export type AlertPreference = z.infer<typeof alertPreferenceSchema>;

export const DEVICE_PLATFORMS = ["ios", "android"] as const;

export const devicePushTokenSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  platform: z.enum(DEVICE_PLATFORMS),
  token: z.string(),
  created_at: z.string(),
  last_seen: z.string(),
});
export type DevicePushToken = z.infer<typeof devicePushTokenSchema>;

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

export const MEMORY_SCOPES = ["user", "thread"] as const;
export type MemoryScope = (typeof MEMORY_SCOPES)[number];

/** The full backend enum includes `"watchlist"`, but only these three are writable via `/memory`
 * (watchlists are managed through the relational `/watchlists` API instead). */
export const WRITABLE_MEMORY_TYPES = ["preference", "finding", "entity_note"] as const;
export type WritableMemoryType = (typeof WRITABLE_MEMORY_TYPES)[number];
export const MEMORY_TYPES = ["watchlist", ...WRITABLE_MEMORY_TYPES] as const;
export type MemoryType = (typeof MEMORY_TYPES)[number];

export const memorySchema = z.object({
  id: z.string(),
  user_id: z.string(),
  scope: z.enum(MEMORY_SCOPES),
  thread_id: z.string().nullable(),
  type: z.enum(MEMORY_TYPES),
  key: z.string(),
  content: z.record(z.string(), z.unknown()).default({}),
  source_ref: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
});
export type Memory = z.infer<typeof memorySchema>;

export const memoriesResponseSchema = z.object({
  memories: z.array(memorySchema).default([]),
});

// ---------------------------------------------------------------------------
// The universal `{code, message, retryable}` error envelope, re-exported here for convenience —
// canonical definition is `lib/api/schemas.ts::apiErrorSchema`.
// ---------------------------------------------------------------------------

export { TIERS };
