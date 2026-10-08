import type { UpdateAlertRequest } from "@/lib/api/settings";
import { parseServerDate } from "@/lib/parse-server-date";
import {
  ALERT_TEMPLATE_KINDS,
  type AlertCondition,
  type AlertMarket,
  type AlertRule,
  type AlertTemplateKind,
  type AlertTriggerMode,
  alertConditionSchema,
  type INSTITUTIONAL_INVESTORS,
} from "@/types/settings";

/** Pure logic behind the alert form and list — no React, no i18n — so it is unit-testable and the
 * components stay thin. The authoritative bounds live in `alertConditionSchema` (mirroring the
 * backend's pydantic constraints); this module only turns raw form strings into a candidate and
 * lets the schema judge it. */

export type ConditionFieldKey = "value" | "percent" | "multiple" | "window" | "days";
export type ConditionFieldError = "required" | "range";
export type ConditionFieldErrors = Partial<Record<ConditionFieldKey, ConditionFieldError>>;

export type ConditionDraft = {
  kind: AlertTemplateKind;
  comparator: "above" | "below";
  moveDirection: "up" | "down";
  flowDirection: "buy" | "sell";
  marginSide: "margin" | "short";
  navSide: "premium" | "discount";
  investor: (typeof INSTITUTIONAL_INVESTORS)[number];
  value: string;
  percent: string;
  multiple: string;
  window: string;
  days: string;
};

export const DEFAULT_DRAFT: ConditionDraft = {
  kind: "close_price",
  comparator: "above",
  moveDirection: "up",
  flowDirection: "buy",
  marginSide: "margin",
  navSide: "premium",
  investor: "foreign",
  value: "",
  percent: "",
  multiple: "",
  window: "20",
  days: "3",
};

export type BuildResult =
  | { ok: true; condition: AlertCondition }
  | { ok: false; errors: ConditionFieldErrors };

const FIELD_KEYS: readonly ConditionFieldKey[] = ["value", "percent", "multiple", "window", "days"];

/** Decimal input → number. Empty or non-numeric is "required", never silently 0. Decimals are kept:
 * v1 truncated every threshold to an integer, so a 123.5 price level was unrepresentable. */
function parseDecimal(raw: string): number | null {
  const trimmed = raw.trim();
  if (trimmed === "") {
    return null;
  }
  const n = Number(trimmed);
  return Number.isFinite(n) ? n : null;
}

/** The form fields each template actually uses, in display order. */
export function fieldsFor(kind: AlertTemplateKind): readonly ConditionFieldKey[] {
  switch (kind) {
    case "close_price":
      return ["value"];
    case "daily_change":
    case "margin_balance_change":
    case "etf_premium_discount":
      return ["percent"];
    case "volume_surge":
      return ["multiple", "window"];
    case "institutional_streak":
      return ["days"];
    case "attention_entry":
    case "disposition_entry":
    case "month_revenue_published":
    case "material_announcement":
      return [];
  }
}

function candidateFor(draft: ConditionDraft, nums: Record<ConditionFieldKey, number | null>) {
  switch (draft.kind) {
    case "close_price":
      return { kind: draft.kind, comparator: draft.comparator, value: nums.value };
    case "daily_change":
      return { kind: draft.kind, direction: draft.moveDirection, percent: nums.percent };
    case "volume_surge":
      return { kind: draft.kind, multiple: nums.multiple, window: nums.window };
    case "institutional_streak":
      return {
        kind: draft.kind,
        investor: draft.investor,
        direction: draft.flowDirection,
        days: nums.days,
      };
    case "margin_balance_change":
      return {
        kind: draft.kind,
        side: draft.marginSide,
        direction: draft.moveDirection,
        percent: nums.percent,
      };
    case "etf_premium_discount":
      return { kind: draft.kind, direction: draft.navSide, percent: nums.percent };
    case "attention_entry":
    case "disposition_entry":
    case "month_revenue_published":
    case "material_announcement":
      return { kind: draft.kind };
  }
}

export function buildCondition(draft: ConditionDraft): BuildResult {
  const nums: Record<ConditionFieldKey, number | null> = {
    value: parseDecimal(draft.value),
    percent: parseDecimal(draft.percent),
    multiple: parseDecimal(draft.multiple),
    window: parseDecimal(draft.window),
    days: parseDecimal(draft.days),
  };

  const errors: ConditionFieldErrors = {};
  for (const key of fieldsFor(draft.kind)) {
    if (nums[key] === null) {
      errors[key] = "required";
    }
  }
  if (Object.keys(errors).length > 0) {
    return { ok: false, errors };
  }

  const parsed = alertConditionSchema.safeParse(candidateFor(draft, nums));
  if (parsed.success) {
    return { ok: true, condition: parsed.data };
  }
  for (const issue of parsed.error.issues) {
    const key = issue.path[0];
    const field = FIELD_KEYS.find((k) => k === key);
    if (field) {
      errors[field] = "range";
    }
  }
  return { ok: false, errors };
}

/** i18n key (under `settings.alerts.summary`) and ICU values for a rule's one-line description.
 * Every variant has its own fully-written message, so no sentence is composed at runtime. */
export function conditionSummary(condition: AlertCondition): {
  key: string;
  values: Record<string, number>;
} {
  switch (condition.kind) {
    case "close_price":
      return { key: `close_price.${condition.comparator}`, values: { value: condition.value } };
    case "daily_change":
      return {
        key: `daily_change.${condition.direction}`,
        values: { percent: condition.percent },
      };
    case "volume_surge":
      return {
        key: "volume_surge",
        values: { multiple: condition.multiple, window: condition.window },
      };
    case "institutional_streak":
      return {
        key: `institutional_streak.${condition.investor}.${condition.direction}`,
        values: { days: condition.days },
      };
    case "margin_balance_change":
      return {
        key: `margin_balance_change.${condition.side}.${condition.direction}`,
        values: { percent: condition.percent },
      };
    case "etf_premium_discount":
      return {
        key: `etf_premium_discount.${condition.direction}`,
        values: { percent: condition.percent },
      };
    case "attention_entry":
    case "disposition_entry":
    case "month_revenue_published":
    case "material_announcement":
      return { key: condition.kind, values: {} };
  }
}

export type RuleStatus = "active" | "paused" | "expired" | "completed";

/** UI status from the rule's fields (docs/backend/modules/14 §3): 已觸發 maps to a `once` rule the
 * engine auto-disabled (`completed`), 已過期 to an expired rule, 已暫停 to any other disabled one. */
export function ruleStatus(rule: AlertRule, now: Date): RuleStatus {
  if (rule.enabled) {
    return "active";
  }
  if (rule.auto_paused_reason === "completed") {
    return "completed";
  }
  const expiredByDate = rule.expires_at !== null && parseServerDate(rule.expires_at) < now;
  if (rule.auto_paused_reason === "expired" || expiredByDate) {
    return "expired";
  }
  return "paused";
}

/** The inverse of `buildCondition`: a stored condition back into form strings, for editing. */
export function draftFromCondition(condition: AlertCondition): ConditionDraft {
  const base: ConditionDraft = { ...DEFAULT_DRAFT, kind: condition.kind };
  switch (condition.kind) {
    case "close_price":
      return { ...base, comparator: condition.comparator, value: String(condition.value) };
    case "daily_change":
      return { ...base, moveDirection: condition.direction, percent: String(condition.percent) };
    case "volume_surge":
      return {
        ...base,
        multiple: String(condition.multiple),
        window: String(condition.window),
      };
    case "institutional_streak":
      return {
        ...base,
        investor: condition.investor,
        flowDirection: condition.direction,
        days: String(condition.days),
      };
    case "margin_balance_change":
      return {
        ...base,
        marginSide: condition.side,
        moveDirection: condition.direction,
        percent: String(condition.percent),
      };
    case "etf_premium_discount":
      return { ...base, navSide: condition.direction, percent: String(condition.percent) };
    case "attention_entry":
    case "disposition_entry":
    case "month_revenue_published":
    case "material_announcement":
      return base;
  }
}

function canonical(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonical).join(",")}]`;
  }
  if (typeof value === "object" && value !== null) {
    const entries = Object.entries(value as Record<string, unknown>).sort(
      ([a], [b]) => (a < b ? -1 : 1), // object keys are unique, so equality cannot occur
    );
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonical(v)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/** Key-order-insensitive deep equality for two conditions. */
export function sameCondition(a: AlertCondition, b: AlertCondition): boolean {
  return canonical(a) === canonical(b);
}

/** `keep` leaves the rule's expiry untouched (its exact date is not one of the presets). */
export type ExpiryChoice = "keep" | "30" | "90" | "365" | "never";

export type EditInputs = {
  condition: AlertCondition;
  triggerMode: AlertTriggerMode;
  expiry: ExpiryChoice;
};

/** The minimal PATCH for an edit, or null when nothing changed. Only changed fields are sent: the
 * backend resets a rule's edge-trigger memory whenever a condition is sent (comparing "true now"
 * to "true last time" is meaningless across a different condition), so re-sending an unchanged
 * condition would needlessly re-arm the alert. `expected_version` guards against a stale edit. */
export function buildAlertPatch(rule: AlertRule, next: EditInputs): UpdateAlertRequest | null {
  const patch: UpdateAlertRequest = { expected_version: rule.version };
  let changed = false;
  if (!sameCondition(rule.condition, next.condition)) {
    patch.condition = next.condition;
    changed = true;
  }
  if (rule.trigger_mode !== next.triggerMode) {
    patch.trigger_mode = next.triggerMode;
    changed = true;
  }
  if (next.expiry !== "keep") {
    patch.expires_in_days = next.expiry === "never" ? null : Number(next.expiry);
    changed = true;
  }
  return changed ? patch : null;
}

/** Templates backed by Taiwan-only data (三大法人, exchange watch lists, 月營收). Must equal the
 * backend's `TW_ONLY_KINDS` — asserted against the captured contract fixture, so drift fails a
 * test instead of offering a US user a template the API will refuse. */
export const TW_ONLY_TEMPLATE_KINDS: readonly AlertTemplateKind[] = [
  "institutional_streak",
  "attention_entry",
  "disposition_entry",
  "month_revenue_published",
  "material_announcement",
  "margin_balance_change",
  "etf_premium_discount",
];

/** The templates that have data for `market` — a US symbol is not offered Taiwan-only ones. */
export function templatesFor(market: AlertMarket): readonly AlertTemplateKind[] {
  return market === "TW"
    ? ALERT_TEMPLATE_KINDS
    : ALERT_TEMPLATE_KINDS.filter((kind) => !TW_ONLY_TEMPLATE_KINDS.includes(kind));
}

/** Templates whose level is in a stock's own units (a price). They read differently for every
 * stock, so a watchlist-group rule does not offer them — they are set per stock. Must equal the
 * backend's `PER_STOCK_KINDS`. */
export const PER_STOCK_TEMPLATE_KINDS: readonly AlertTemplateKind[] = ["close_price"];

/** The templates a watchlist-group rule can use: the scale-free ones (a percent, a multiple, a day
 * count, a list entry) that mean the same thing for every member. */
export function groupTemplatesFor(market: AlertMarket): readonly AlertTemplateKind[] {
  return templatesFor(market).filter((kind) => !PER_STOCK_TEMPLATE_KINDS.includes(kind));
}

export type StaleMerge = {
  condition: AlertCondition;
  triggerMode: AlertTriggerMode;
  /** True when the user and the other device both changed the same field — the user's value won. */
  conflict: boolean;
};

/** Three-way merge after a 409: `base` is the rule as the editor opened it, `theirs` the latest
 * server copy, `mine` the form's current values. A field only the other side changed takes
 * theirs; a field the user touched keeps the user's value (flagged when theirs also moved). */
export function mergeStaleEdit(
  base: AlertRule,
  theirs: AlertRule,
  mine: { condition: AlertCondition; triggerMode: AlertTriggerMode },
): StaleMerge {
  const mineCondition = !sameCondition(base.condition, mine.condition);
  const theirCondition = !sameCondition(base.condition, theirs.condition);
  const mineMode = base.trigger_mode !== mine.triggerMode;
  const theirMode = base.trigger_mode !== theirs.trigger_mode;
  return {
    condition: mineCondition ? mine.condition : theirs.condition,
    triggerMode: mineMode ? mine.triggerMode : theirs.trigger_mode,
    conflict: (mineCondition && theirCondition) || (mineMode && theirMode),
  };
}
