/**
 * Settings API client — profile, connections, personality, pets, billing, BYOK keys, usage quota,
 * schedules, alerts, notifications, alert-preferences, devices, memory.
 *
 * Mirrors `backend/src/api/routers/accounts_extra.py`, `auth.py` (`/me/connections`), `pets.py`,
 * `billing.py`, `scheduler.py`, `memory.py`. All requests are credentialed (session cookie);
 * every response is Zod-validated at the boundary (parse-don't-trust). Every non-2xx body is the
 * shared `{code, message, retryable}` envelope — `SettingsApiError` carries `code` through so a
 * caller can branch on a specific backend error (e.g. `no_pulls_available`, `last_login_method`)
 * instead of only having a generic message.
 */

import { apiUrl } from "@/lib/api/config";
import { apiErrorSchema } from "@/lib/api/schemas";
import {
  type Account,
  type EntitlementView,
  entitlementViewSchema,
  userSchema,
} from "@/types/account";
import {
  type AlertPreference,
  type AlertRule,
  alertPreferenceSchema,
  alertRuleSchema,
  alertsResponseSchema,
  type BillingTransaction,
  billingHistoryResponseSchema,
  type CheckoutResponse,
  checkoutResponseSchema,
  type DeliveryChannelKind,
  type DevicePushToken,
  devicePushTokenSchema,
  type Memory,
  type MemoryScope,
  memoriesResponseSchema,
  memorySchema,
  type Notification,
  type NotificationBadge,
  notificationBadgeSchema,
  notificationsResponseSchema,
  type OAuthAccountLike,
  type OwnedPet,
  oauthAccountSchema,
  ownedPetSchema,
  type PersonalityPreset,
  type PetCatalogEntry,
  type PetProgress,
  type PlansResponse,
  type PortalResponse,
  type PullOutcome,
  personalityPresetsResponseSchema,
  petCatalogEntrySchema,
  petProgressSchema,
  plansResponseSchema,
  portalResponseSchema,
  pullOutcomeSchema,
  type QuietHours,
  type ScheduleAction,
  type ScheduleJob,
  type ScheduleTrigger,
  type SecretKind,
  type SubscriptionResponse,
  scheduleJobSchema,
  schedulesResponseSchema,
  subscriptionResponseSchema,
} from "@/types/settings";

export class SettingsApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super(message);
    this.name = "SettingsApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}

async function requestJson(
  path: string,
  init: RequestInit & { expectBody?: boolean } = {},
): Promise<unknown> {
  const { expectBody = true, ...rest } = init;
  const response = await fetch(apiUrl(path), {
    credentials: "include",
    headers: { accept: "application/json", ...rest.headers },
    ...rest,
  });
  if (!response.ok) {
    const parsed = apiErrorSchema.safeParse(await response.json().catch(() => null));
    if (parsed.success) {
      throw new SettingsApiError(
        parsed.data.code,
        parsed.data.message,
        response.status,
        parsed.data.retryable,
      );
    }
    throw new SettingsApiError(
      "unknown_error",
      `Request failed (${response.status})`,
      response.status,
      response.status >= 500,
    );
  }
  if (!expectBody || response.status === 204) {
    return null;
  }
  return response.json();
}

function jsonHeaders(): HeadersInit {
  return { "content-type": "application/json" };
}

// ---------------------------------------------------------------------------
// Profile — PATCH /me
// ---------------------------------------------------------------------------

export type UpdateProfileRequest = Partial<{
  display_name: string | null;
  birthday: string | null;
  phone_country_code: string | null;
  phone_number: string | null;
  nationality: string | null;
  personality_id: string | null;
}>;

export type ProfileResponse = { user: Account; entitlements: EntitlementView };

export async function updateProfile(body: UpdateProfileRequest): Promise<ProfileResponse> {
  const raw = await requestJson("/me", {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  const record = raw as { user: unknown; entitlements: unknown };
  return {
    user: userSchema.parse(record.user),
    entitlements: entitlementViewSchema.parse(record.entitlements),
  };
}

// ---------------------------------------------------------------------------
// Connections — GET/DELETE /me/connections
// ---------------------------------------------------------------------------

export async function listConnections(): Promise<OAuthAccountLike[]> {
  const raw = (await requestJson("/me/connections")) as { connections: unknown[] };
  return raw.connections.map((entry) => oauthAccountSchema.parse(entry));
}

export async function unlinkConnection(provider: "google" | "line"): Promise<void> {
  await requestJson(`/me/connections/${provider}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Personality presets
// ---------------------------------------------------------------------------

export async function listPersonalityPresets(): Promise<PersonalityPreset[]> {
  const raw = await requestJson("/personality/presets");
  return personalityPresetsResponseSchema.parse(raw).presets;
}

// ---------------------------------------------------------------------------
// Pets
// ---------------------------------------------------------------------------

export async function listPetCatalog(): Promise<PetCatalogEntry[]> {
  const raw = (await requestJson("/pets/catalog")) as { catalog: unknown[] };
  return raw.catalog.map((entry) => petCatalogEntrySchema.parse(entry));
}

export async function listOwnedPets(): Promise<OwnedPet[]> {
  const raw = (await requestJson("/pets")) as { pets: unknown[] };
  return raw.pets.map((pet) => ownedPetSchema.parse(pet));
}

export async function getActivePet(): Promise<OwnedPet | null> {
  const raw = (await requestJson("/pets/active")) as { active: unknown | null };
  return raw.active ? ownedPetSchema.parse(raw.active) : null;
}

export async function equipPet(recordId: string): Promise<OwnedPet> {
  const raw = await requestJson(`/pets/${encodeURIComponent(recordId)}/equip`, { method: "PUT" });
  return ownedPetSchema.parse(raw);
}

export async function getPetProgress(): Promise<PetProgress> {
  const raw = await requestJson("/pets/progress");
  return petProgressSchema.parse(raw);
}

/** `409 no_pulls_available` is an expected outcome, not exceptional — callers should disable the
 * pull action when `progress.available_pulls === 0` rather than relying on this throwing. */
export async function pullPet(): Promise<PullOutcome> {
  const raw = await requestJson("/pets/pull", { method: "POST" });
  return pullOutcomeSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// Billing
// ---------------------------------------------------------------------------

/** `billing_unconfigured` (503) is the expected state whenever Paddle isn't configured (e.g. any
 * local/dev environment) — callers should render an honest degraded state, not treat it as a bug. */
export async function listPlans(): Promise<PlansResponse> {
  const raw = await requestJson("/billing/plans");
  return plansResponseSchema.parse(raw);
}

export async function checkout(body: {
  tier: "premium" | "pro";
  interval: "monthly" | "annual";
  byok?: boolean;
}): Promise<CheckoutResponse> {
  const raw = await requestJson("/billing/checkout", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return checkoutResponseSchema.parse(raw);
}

export async function getSubscription(): Promise<SubscriptionResponse> {
  const raw = await requestJson("/billing/subscription");
  return subscriptionResponseSchema.parse(raw);
}

export async function openBillingPortal(): Promise<PortalResponse> {
  const raw = await requestJson("/billing/portal", { method: "POST" });
  return portalResponseSchema.parse(raw);
}

export async function getBillingHistory(): Promise<BillingTransaction[]> {
  const raw = await requestJson("/billing/history");
  return billingHistoryResponseSchema.parse(raw).transactions;
}

// ---------------------------------------------------------------------------
// BYOK keys — POST/DELETE /me/keys (204 No Content on both)
// ---------------------------------------------------------------------------

export async function putKey(body: {
  provider: string;
  kind?: SecretKind;
  key: string;
}): Promise<void> {
  await requestJson("/me/keys", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
    expectBody: false,
  });
}

export async function removeKey(provider: string, kind: SecretKind = "llm_key"): Promise<void> {
  await requestJson(`/me/keys/${encodeURIComponent(provider)}?kind=${kind}`, {
    method: "DELETE",
    expectBody: false,
  });
}

// ---------------------------------------------------------------------------
// Usage quota — GET /usage/quota (limits only, no live consumption)
// ---------------------------------------------------------------------------

export async function getUsageQuota(): Promise<EntitlementView> {
  const raw = await requestJson("/usage/quota");
  return entitlementViewSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// Schedules (Pro-gated — 403 feature_not_entitled for Free/Premium)
// ---------------------------------------------------------------------------

export async function listSchedules(): Promise<ScheduleJob[]> {
  const raw = await requestJson("/schedules");
  return schedulesResponseSchema.parse(raw).schedules;
}

export type CreateScheduleRequest = {
  trigger: ScheduleTrigger;
  action: ScheduleAction;
  delivery_channels?: DeliveryChannelKind[];
  quiet_hours?: QuietHours | null;
  daily_limit?: number | null;
};

export async function createSchedule(body: CreateScheduleRequest): Promise<ScheduleJob> {
  const raw = await requestJson("/schedules", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return scheduleJobSchema.parse(raw);
}

export async function updateSchedule(
  jobId: string,
  body: Partial<{
    enabled: boolean;
    delivery_channels: DeliveryChannelKind[];
    daily_limit: number | null;
  }>,
): Promise<ScheduleJob> {
  const raw = await requestJson(`/schedules/${encodeURIComponent(jobId)}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return scheduleJobSchema.parse(raw);
}

export async function deleteSchedule(jobId: string): Promise<void> {
  await requestJson(`/schedules/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
    expectBody: false,
  });
}

// ---------------------------------------------------------------------------
// Alerts (Pro-gated)
// ---------------------------------------------------------------------------

export async function listAlerts(): Promise<AlertRule[]> {
  const raw = await requestJson("/alerts");
  return alertsResponseSchema.parse(raw).alerts;
}

export type CreateAlertRequest = {
  symbol: string;
  market: ScheduleTrigger["market"];
  condition: AlertRule["condition"];
  channels?: DeliveryChannelKind[];
  quiet_hours?: QuietHours | null;
  daily_limit?: number | null;
};

export async function createAlert(body: CreateAlertRequest): Promise<AlertRule> {
  const raw = await requestJson("/alerts", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return alertRuleSchema.parse(raw);
}

/** Only `enabled` is patchable — the backend does not support editing symbol/condition/channels
 * on an existing rule; changing those requires delete + re-create. */
export async function updateAlert(alertId: string, enabled: boolean): Promise<AlertRule> {
  const raw = await requestJson(`/alerts/${encodeURIComponent(alertId)}`, {
    method: "PATCH",
    headers: jsonHeaders(),
    body: JSON.stringify({ enabled }),
  });
  return alertRuleSchema.parse(raw);
}

export async function deleteAlert(alertId: string): Promise<void> {
  await requestJson(`/alerts/${encodeURIComponent(alertId)}`, {
    method: "DELETE",
    expectBody: false,
  });
}

// ---------------------------------------------------------------------------
// Notifications (not tier-gated)
// ---------------------------------------------------------------------------

export async function listNotifications(options?: {
  unreadOnly?: boolean;
  limit?: number;
}): Promise<Notification[]> {
  const params = new URLSearchParams();
  if (options?.unreadOnly) params.set("unread_only", "true");
  if (options?.limit) params.set("limit", String(options.limit));
  const suffix = params.toString() ? `?${params.toString()}` : "";
  const raw = await requestJson(`/notifications${suffix}`);
  return notificationsResponseSchema.parse(raw).notifications;
}

export async function getNotificationBadge(): Promise<NotificationBadge> {
  const raw = await requestJson("/notifications/badge");
  return notificationBadgeSchema.parse(raw);
}

// ---------------------------------------------------------------------------
// Alert preferences + devices
// ---------------------------------------------------------------------------

export type AlertPreferenceRequest = Partial<{
  channels: DeliveryChannelKind[];
  enabled_categories: string[];
  quiet_hours: QuietHours | null;
  daily_limit: number | null;
  morning_digest: boolean;
}>;

export async function putAlertPreferences(body: AlertPreferenceRequest): Promise<AlertPreference> {
  const raw = await requestJson("/me/alert-preferences", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return alertPreferenceSchema.parse(raw);
}

export async function registerDevice(body: {
  platform: "ios" | "android";
  token: string;
}): Promise<DevicePushToken> {
  const raw = await requestJson("/me/devices", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return devicePushTokenSchema.parse(raw);
}

export async function removeDevice(token: string): Promise<void> {
  await requestJson(`/me/devices/${encodeURIComponent(token)}`, {
    method: "DELETE",
    expectBody: false,
  });
}

// ---------------------------------------------------------------------------
// Memory
// ---------------------------------------------------------------------------

export async function listMemories(scope?: MemoryScope): Promise<Memory[]> {
  const suffix = scope ? `?scope=${scope}` : "";
  const raw = await requestJson(`/memory${suffix}`);
  return memoriesResponseSchema.parse(raw).memories;
}

export type UpsertMemoryRequest = {
  scope?: MemoryScope;
  type?: "preference" | "finding" | "entity_note";
  key: string;
  content?: Record<string, unknown>;
  thread_id?: string | null;
  source_ref?: string | null;
};

export async function createMemory(body: UpsertMemoryRequest): Promise<Memory> {
  const raw = await requestJson("/memory", {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return memorySchema.parse(raw);
}

export async function deleteMemory(memoryId: string): Promise<void> {
  await requestJson(`/memory/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
    expectBody: false,
  });
}
