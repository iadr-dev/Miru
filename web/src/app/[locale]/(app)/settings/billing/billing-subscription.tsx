"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SettingsRow } from "@/components/settings/settings-section";
import { SettingsError } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { formatDate, formatMinorAmount } from "@/lib/billing-format";
import {
  changeBlockReason,
  findPlan,
  isLiveSubscription,
  type SubscriptionNotice,
  selectionFor,
} from "@/lib/billing-state";
import type { PlanInfo, SubscriptionRecord } from "@/types/settings";
import { type BillingInterval, type PayableTier, PlansForm } from "./plans-form";

/** The tier / interval / BYOK picker state the billing page owns and hands to `PlansForm`. */
export type PlanPicker = {
  readonly tier: PayableTier;
  readonly setTier: (tier: PayableTier) => void;
  readonly interval: BillingInterval;
  readonly setInterval: (interval: BillingInterval) => void;
  readonly byok: boolean;
  readonly setByok: (byok: boolean) => void;
  readonly actionError: string | null;
  readonly locale: string;
};

export function SubscriptionBody({
  failed,
  onRetry,
  subscription,
  plans,
  locale,
  portalPending,
  onManage,
}: {
  readonly failed: boolean;
  readonly onRetry: () => void;
  readonly subscription: SubscriptionRecord | null;
  readonly plans: readonly PlanInfo[];
  readonly locale: string;
  readonly portalPending: boolean;
  readonly onManage: () => void;
}): ReactNode {
  const t = useTranslations("settings.billing");
  const tc = useTranslations("settings.common");
  if (failed) {
    return <SettingsError message={tc("errorLoad")} onRetry={onRetry} />;
  }
  if (subscription && isLiveSubscription(subscription)) {
    return (
      <SettingsRow label={t("currentPlan")} hint={subscriptionHint(t, subscription, plans, locale)}>
        <Button variant="outline" size="sm" disabled={portalPending} onClick={onManage}>
          {t("managePlan")}
        </Button>
      </SettingsRow>
    );
  }
  return <p className="text-[13px] text-[var(--text-muted)]">{t("noSubscription")}</p>;
}

/** The one thing a subscriber should be told right now, with the button that resolves it. */
export function NoticeRow({
  notice,
  subscription,
  locale,
  portalPending,
  resumePending,
  onUpdatePayment,
  onResume,
}: {
  readonly notice: NonNullable<SubscriptionNotice>;
  readonly subscription: SubscriptionRecord | null;
  readonly locale: string;
  readonly portalPending: boolean;
  readonly resumePending: boolean;
  readonly onUpdatePayment: () => void;
  readonly onResume: () => void;
}): ReactNode {
  const t = useTranslations("settings.billing");
  return (
    <div className="flex items-center justify-between gap-3 border-[var(--border)] border-t py-3">
      <p role="status" className="text-[13px] text-[var(--warn)]">
        {noticeText(t, notice, subscription, locale)}
      </p>
      {notice === "pastDue" ? (
        <Button variant="outline" size="sm" disabled={portalPending} onClick={onUpdatePayment}>
          {t("updatePayment")}
        </Button>
      ) : (
        <Button variant="outline" size="sm" disabled={resumePending} onClick={onResume}>
          {t("resumeSubscription")}
        </Button>
      )}
    </div>
  );
}

function noticeText(
  t: (key: string, values?: Record<string, string>) => string,
  notice: NonNullable<SubscriptionNotice>,
  subscription: SubscriptionRecord | null,
  locale: string,
): string {
  if (notice === "pastDue") {
    return t("pastDueNotice");
  }
  if (notice === "paused") {
    return t("pausedNotice");
  }
  return t("cancelScheduledNotice", {
    date: formatDate(subscription?.scheduled_cancel_at, locale) ?? "",
  });
}

export function PlansBody({
  unconfigured,
  subscriptionFailed,
  subscription,
  plans,
  picker,
  changePending,
  onChange,
  checkoutPending,
  onCheckout,
}: {
  readonly unconfigured: boolean;
  readonly subscriptionFailed: boolean;
  readonly subscription: SubscriptionRecord | null;
  readonly plans: readonly PlanInfo[];
  readonly picker: PlanPicker;
  readonly changePending: boolean;
  readonly onChange: () => void;
  readonly checkoutPending: boolean;
  readonly onCheckout: () => void;
}): ReactNode {
  const t = useTranslations("settings.billing");
  if (unconfigured) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("unconfigured")}</p>;
  }
  if (subscriptionFailed) {
    return null;
  }
  const selectedPlan = findPlan(plans, picker);
  if (isLiveSubscription(subscription)) {
    return (
      <ChangePlanForm
        subscription={subscription}
        selectedPlan={selectedPlan}
        picker={picker}
        pending={changePending}
        onSubmit={onChange}
      />
    );
  }
  return (
    <PlansForm
      {...picker}
      pending={checkoutPending}
      onSubmit={onCheckout}
      submitLabel={t("checkout")}
      plan={selectedPlan}
    />
  );
}

function ChangePlanForm({
  subscription,
  selectedPlan,
  picker,
  pending,
  onSubmit,
}: {
  readonly subscription: SubscriptionRecord | null;
  readonly selectedPlan: PlanInfo | undefined;
  readonly picker: PlanPicker;
  readonly pending: boolean;
  readonly onSubmit: () => void;
}): ReactNode {
  const t = useTranslations("settings.billing");
  // A subscriber changes plan here (Paddle's portal only offers card updates and cancelling); a
  // second checkout would bill them twice, so the server refuses it too.
  const block = subscription ? changeBlockReason(subscription, selectedPlan) : null;
  return (
    <PlansForm
      {...picker}
      pending={pending}
      onSubmit={onSubmit}
      submitLabel={t("changePlan")}
      blockedNote={block ? t(`changeBlocked.${block}`) : null}
      confirmText={confirmChangeText(t, picker, selectedPlan)}
      plan={selectedPlan}
    />
  );
}

function confirmChangeText(
  t: (key: string, values?: Record<string, string>) => string,
  picker: PlanPicker,
  selectedPlan: PlanInfo | undefined,
): string {
  const byokSuffix = picker.byok ? ` · ${t("byok")}` : "";
  const tierName = t(`tierOption.${picker.tier}`);
  const intervalName = t(`intervalOption.${picker.interval}`);
  const planLabel = `${tierName} · ${intervalName}${byokSuffix}`;
  return t("confirmChangeText", {
    plan: selectedPlan?.price
      ? `${planLabel} (${formatMinorAmount(selectedPlan.price.amount, selectedPlan.price.currency_code, picker.locale)})`
      : planLabel,
  });
}

/** "Premium · 生效中 · renews on <date>" (or "cancels on <date>"): the facts a subscriber looks
 * for, from the local subscription row (which the webhook keeps current). */
function subscriptionHint(
  t: (key: string, values?: Record<string, string>) => string,
  subscription: SubscriptionRecord,
  plans: readonly PlanInfo[],
  locale: string,
): string {
  // The plan being paid for comes from its price; `subscription.tier` is what it entitles right
  // now, which is `free` while paused.
  const plan = selectionFor(plans, subscription.price_id);
  const planName = plan ? t(`tierOption.${plan.tier}`) : null;
  const parts = [
    [planName, (plan?.byok ?? subscription.byok) ? t("byok") : null].filter(Boolean).join(" · "),
    statusLabel(t, subscription.status),
  ].filter(Boolean);
  const cancelOn = formatDate(subscription.scheduled_cancel_at, locale);
  const renewOn = formatDate(subscription.current_period_end, locale);
  if (cancelOn) {
    parts.push(t("cancelsOn", { date: cancelOn }));
  } else if (renewOn && subscription.status !== "paused" && subscription.status !== "past_due") {
    // Paused: not billed. Past due: the last charge failed and the date on file is the old one.
    // Neither has a next charge to announce.
    parts.push(t("renewsOn", { date: renewOn }));
  }
  return parts.join(" · ");
}

/** Paddle's own subscription-status vocabulary (active/trialing/past_due/paused/canceled) is a
 * fixed, small set — labeled the same way `cardTypeLabel` labels a `card_type`, falling back to
 * the raw status string for a value Paddle adds later that this list hasn't caught up to yet. */
const KNOWN_SUBSCRIPTION_STATUSES = [
  "active",
  "trialing",
  "past_due",
  "paused",
  "canceled",
] as const;

function statusLabel(t: (key: string) => string, status: string): string {
  return (KNOWN_SUBSCRIPTION_STATUSES as readonly string[]).includes(status)
    ? t(`statusOption.${status}`)
    : status;
}
