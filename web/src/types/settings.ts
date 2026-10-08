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

export const OAUTH_LINK_PROVIDERS = ["google", "line", "telegram"] as const;

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

/** A price as Paddle states it: `amount` in the currency's minor units (a string). */
export const planPriceSchema = z.object({ amount: z.string(), currency_code: z.string() });

export const planInfoSchema = z.object({
  price_id: z.string(),
  tier: z.enum(PAYABLE_TIERS),
  interval: z.enum(BILLING_INTERVALS),
  byok: z.boolean(),
  price: planPriceSchema.nullable().default(null),
});
export type PlanInfo = z.infer<typeof planInfoSchema>;

/** One tier as the pricing page shows it - mirrors `accounts.entitlements.TierSummary`. */
export const tierSummarySchema = z.object({
  tier: z.enum(["free", ...PAYABLE_TIERS]),
  chat_limit_per_day: z.number().int().nullable(),
  requests_per_minute: z.number().int(),
  sponsor_data: z.boolean(),
  features: z.array(z.string()).default([]),
  deep_research_limit_per_day: z.number().int().nullable().default(null),
});
export type TierSummary = z.infer<typeof tierSummarySchema>;

export const plansResponseSchema = z.object({
  client_token: z.string().nullable(),
  environment: z.enum(["sandbox", "production"]).default("sandbox"),
  plans: z.array(planInfoSchema).default([]),
  tiers: z.array(tierSummarySchema).default([]),
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
  tier: z.enum(["free", ...PAYABLE_TIERS]), // the tier it currently entitles (free when paused/ended)
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

export const invoiceResponseSchema = z.object({ url: z.string().nullable() });

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
// Schedules
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
  status_reason: z.string().nullable().default(null),
  thread_id: z.string().nullable().default(null),
  version: z.number().int().default(0),
  created_at: z.string(),
  updated_at: z.string(),
});
export type ScheduleJob = z.infer<typeof scheduleJobSchema>;

export const schedulesResponseSchema = z.object({
  schedules: z.array(scheduleJobSchema).default([]),
});

// ---------------------------------------------------------------------------
// Alerts — mirrors backend/src/scheduler/templates.py + models.py (docs/backend/modules/14 §3).
// Open to every signed-in user (modules/14); the backend caps rules per user (409 `cap_reached`).
// ---------------------------------------------------------------------------

/** Alerts cover TW and US only (the DB CHECK); `MARKETS` keeps GB/JP for the schedule trigger. */
export const ALERT_MARKETS = ["TW", "US"] as const;
export type AlertMarket = (typeof ALERT_MARKETS)[number];

export const ALERT_TEMPLATE_KINDS = [
  "close_price",
  "daily_change",
  "volume_surge",
  "institutional_streak",
  "attention_entry",
  "disposition_entry",
  "month_revenue_published",
  "material_announcement",
  "margin_balance_change",
  "etf_premium_discount",
] as const;
export type AlertTemplateKind = (typeof ALERT_TEMPLATE_KINDS)[number];

export const INSTITUTIONAL_INVESTORS = ["foreign", "trust", "dealer"] as const;

/** The typed `condition {kind, ...params}` union. Thresholds are decimals; bounds match the
 * backend's pydantic `Field` constraints so an out-of-range value fails here before the request. */
export const alertConditionSchema = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("close_price"),
    comparator: z.enum(["above", "below"]),
    value: z.number().gt(0),
  }),
  z.object({
    kind: z.literal("daily_change"),
    direction: z.enum(["up", "down"]),
    percent: z.number().gt(0).max(100),
  }),
  z.object({
    kind: z.literal("volume_surge"),
    multiple: z.number().gt(1).max(100),
    window: z.number().int().min(5).max(60).default(20),
  }),
  z.object({
    kind: z.literal("institutional_streak"),
    investor: z.enum(INSTITUTIONAL_INVESTORS),
    direction: z.enum(["buy", "sell"]),
    days: z.number().int().min(2).max(20),
  }),
  z.object({ kind: z.literal("attention_entry") }),
  z.object({ kind: z.literal("disposition_entry") }),
  z.object({ kind: z.literal("month_revenue_published") }),
  z.object({ kind: z.literal("material_announcement") }),
  z.object({
    kind: z.literal("margin_balance_change"),
    side: z.enum(["margin", "short"]),
    direction: z.enum(["up", "down"]),
    percent: z.number().gt(0).max(1000),
  }),
  z.object({
    kind: z.literal("etf_premium_discount"),
    direction: z.enum(["premium", "discount"]),
    percent: z.number().gt(0).max(50),
  }),
]);
export type AlertCondition = z.infer<typeof alertConditionSchema>;

export const ALERT_TRIGGER_MODES = ["once", "once_per_day", "every_occurrence"] as const;
export type AlertTriggerMode = (typeof ALERT_TRIGGER_MODES)[number];

export const alertRuleSchema = z.object({
  id: z.string(),
  user_id: z.string(),
  // Scope is a symbol+market XOR a watchlist group (the backend enforces the same XOR).
  symbol: z.string().nullable().default(null),
  market: z.enum(MARKETS).nullable().default(null),
  watchlist_id: z.string().nullable().default(null),
  // The group a per-stock rule was set up from (display only; it does not scope the rule).
  origin_watchlist_id: z.string().nullable().default(null),
  condition: alertConditionSchema,
  trigger_mode: z.enum(ALERT_TRIGGER_MODES).default("once_per_day"),
  cooldown_seconds: z.number().int().nullable().default(null),
  state: z.record(z.string(), z.unknown()).default({}),
  expires_at: z.string().nullable().default(null),
  auto_paused_reason: z.string().nullable().default(null),
  explain: z.boolean().default(false),
  channels: z.array(z.enum(DELIVERY_CHANNEL_KINDS)).default([]),
  quiet_hours: quietHoursSchema.nullable(),
  daily_limit: z.number().int().nullable(),
  enabled: z.boolean().default(true),
  last_triggered: z.string().nullable(),
  version: z.number().int().default(0),
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
  morning_digest: z.boolean().default(false),
  // IANA zone the quiet-hours window is evaluated in.
  timezone: z.string().default("Asia/Taipei"),
  updated_at: z.string(),
});
export type AlertPreference = z.infer<typeof alertPreferenceSchema>;

export const DEVICE_PLATFORMS = ["ios", "android", "web"] as const;

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
  last_used_at: z.string().nullable().default(null),
  use_count: z.number().int().default(0),
});
export type Memory = z.infer<typeof memorySchema>;

export const memorySettingsSchema = z.object({ enabled: z.boolean() });
export type MemorySettings = z.infer<typeof memorySettingsSchema>;

export const memoriesResponseSchema = z.object({
  memories: z.array(memorySchema).default([]),
});

// ---------------------------------------------------------------------------
// The universal `{code, message, retryable}` error envelope, re-exported here for convenience —
// canonical definition is `lib/api/schemas.ts::apiErrorSchema`.
// ---------------------------------------------------------------------------

export { TIERS };
