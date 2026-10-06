"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Checkbox, Select } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import {
  checkout,
  getBillingHistory,
  getSubscription,
  listPlans,
  openBillingPortal,
  SettingsApiError,
} from "@/lib/api/settings";
import { formatDate, formatMinorAmount, summarizeTransaction } from "@/lib/billing-format";
import {
  BILLING_INTERVALS,
  type BillingTransaction,
  PAYABLE_TIERS,
  type PlanInfo,
  type SubscriptionRecord,
} from "@/types/settings";

export function BillingPanel(): ReactNode {
  const t = useTranslations("settings.billing");
  const tc = useTranslations("settings.common");

  const locale = useLocale();
  // Paddle prices Taiwan separately (TWD), so a zh-TW UI asks for the Taiwan price.
  const country = locale === "zh-TW" ? "TW" : undefined;
  const plansQuery = useQuery({
    queryKey: ["settings", "plans", country],
    queryFn: () => listPlans(country),
  });
  const subscriptionQuery = useQuery({
    queryKey: ["settings", "subscription"],
    queryFn: getSubscription,
  });

  const [tier, setTier] = useState<(typeof PAYABLE_TIERS)[number]>("premium");
  const [interval, setInterval] = useState<(typeof BILLING_INTERVALS)[number]>("monthly");
  const [byok, setByok] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const checkoutMutation = useMutation({
    mutationFn: () => checkout({ tier, interval, byok }),
    onSuccess: (data) => {
      if (data.checkout_url) {
        window.location.assign(data.checkout_url);
      }
    },
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  const portalMutation = useMutation({
    mutationFn: openBillingPortal,
    onSuccess: (data) => {
      if (data.portal_url) {
        window.location.assign(data.portal_url);
      }
    },
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  const historyQuery = useQuery({
    queryKey: ["settings", "billing-history"],
    queryFn: getBillingHistory,
    retry: false,
  });

  if (plansQuery.isLoading || subscriptionQuery.isLoading) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={3} />
      </SettingsSection>
    );
  }

  const selectedPlan = plansQuery.data?.plans.find(
    (p) => p.tier === tier && p.interval === interval && p.byok === byok,
  );

  const unconfigured =
    plansQuery.isError && plansQuery.error instanceof SettingsApiError
      ? plansQuery.error.code === "billing_unconfigured"
      : !plansQuery.data?.client_token && (plansQuery.data?.plans.length ?? 0) === 0;

  return (
    <>
      <SettingsSection title={t("subscriptionTitle")} className="mb-6">
        {subscriptionQuery.data?.subscription ? (
          <SettingsRow
            label={t("currentPlan")}
            hint={subscriptionHint(t, subscriptionQuery.data.subscription, locale)}
          >
            <Button
              variant="outline"
              size="sm"
              disabled={portalMutation.isPending}
              onClick={() => portalMutation.mutate()}
            >
              {t("managePlan")}
            </Button>
          </SettingsRow>
        ) : (
          <p className="text-[13px] text-[var(--text-muted)]">{t("noSubscription")}</p>
        )}
        <SettingsRow label={t("byokActive")}>
          <span className="text-[13px] text-[var(--text)]">
            {subscriptionQuery.data?.byok_active ? tc("yes") : tc("no")}
          </span>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection title={t("plansTitle")} className="mb-6">
        {unconfigured ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("unconfigured")}</p>
        ) : (
          <PlansForm
            tier={tier}
            setTier={setTier}
            interval={interval}
            setInterval={setInterval}
            byok={byok}
            setByok={setByok}
            actionError={actionError}
            checkoutPending={checkoutMutation.isPending}
            onCheckout={() => checkoutMutation.mutate()}
            plan={selectedPlan}
            locale={locale}
          />
        )}
      </SettingsSection>

      <SettingsSection title={t("historyTitle")}>
        <BillingHistorySection query={historyQuery} locale={locale} />
      </SettingsSection>
    </>
  );
}

type PlansFormProps = {
  tier: (typeof PAYABLE_TIERS)[number];
  setTier: (tier: (typeof PAYABLE_TIERS)[number]) => void;
  interval: (typeof BILLING_INTERVALS)[number];
  setInterval: (interval: (typeof BILLING_INTERVALS)[number]) => void;
  byok: boolean;
  setByok: (byok: boolean) => void;
  actionError: string | null;
  checkoutPending: boolean;
  onCheckout: () => void;
  plan: PlanInfo | undefined;
  locale: string;
};

function PlansForm({
  tier,
  setTier,
  interval,
  setInterval,
  byok,
  setByok,
  actionError,
  checkoutPending,
  onCheckout,
  plan,
  locale,
}: PlansFormProps): ReactNode {
  const t = useTranslations("settings.billing");
  return (
    <div className="space-y-4 py-1">
      <SettingsRow label={t("tier")}>
        <Select
          aria-label={t("tier")}
          value={tier}
          onChange={(e) => setTier(e.target.value as (typeof PAYABLE_TIERS)[number])}
          className="w-40"
        >
          {PAYABLE_TIERS.map((v) => (
            <option key={v} value={v}>
              {t(`tierOption.${v}`)}
            </option>
          ))}
        </Select>
      </SettingsRow>
      <SettingsRow label={t("interval")}>
        <Select
          aria-label={t("interval")}
          value={interval}
          onChange={(e) => setInterval(e.target.value as (typeof BILLING_INTERVALS)[number])}
          className="w-40"
        >
          {BILLING_INTERVALS.map((v) => (
            <option key={v} value={v}>
              {t(`intervalOption.${v}`)}
            </option>
          ))}
        </Select>
      </SettingsRow>
      <SettingsRow label={t("byok")}>
        <Checkbox checked={byok} onChange={setByok} label={t("byokCheckbox")} />
      </SettingsRow>
      <SettingsRow label={t("price")} hint={plan?.price ? t("taxNote") : undefined}>
        <span className="font-medium text-[14px] text-[var(--text)] tabular-nums">
          {plan?.price
            ? `${formatMinorAmount(plan.price.amount, plan.price.currency_code, locale)} / ${t(`perInterval.${interval}`)}`
            : t("priceUnavailable")}
        </span>
      </SettingsRow>
      {actionError ? (
        <p role="alert" className="text-[13px] text-[var(--warn)]">
          {actionError}
        </p>
      ) : null}
      <Button variant="filled" size="sm" disabled={checkoutPending} onClick={onCheckout}>
        {t("checkout")}
      </Button>
    </div>
  );
}

function BillingHistorySection({
  query,
  locale,
}: {
  query: ReturnType<typeof useQuery<BillingTransaction[]>>;
  locale: string;
}): ReactNode {
  const t = useTranslations("settings.billing");
  const tc = useTranslations("settings.common");

  if (query.isLoading) {
    return <SettingsLoading rows={2} />;
  }
  if (query.isError) {
    const unconfigured =
      query.error instanceof SettingsApiError && query.error.code === "billing_unconfigured";
    return (
      <p className="text-[13px] text-[var(--text-muted)]">
        {unconfigured ? t("unconfigured") : tc("errorLoad")}
      </p>
    );
  }
  const transactions = query.data ?? [];
  if (transactions.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("noHistory")}</p>;
  }
  return <HistoryList transactions={transactions} locale={locale} />;
}

function HistoryList({
  transactions,
  locale,
}: {
  transactions: BillingTransaction[];
  locale: string;
}): ReactNode {
  const t = useTranslations("settings.billing");
  return (
    <ul className="divide-y divide-[var(--border)]">
      {transactions.map((tx, i) => {
        const row = summarizeTransaction(tx, i, locale);
        return (
          <li key={row.key} className="flex items-center justify-between gap-3 py-2.5 text-[13px]">
            <span className="text-[var(--text-muted)] tabular-nums">{row.date ?? "—"}</span>
            <span className="font-medium text-[var(--text)] tabular-nums">{row.amount ?? "—"}</span>
            <span className="text-[var(--text-muted)]">
              {row.status ? transactionStatusLabel(t, row.status) : "—"}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

const KNOWN_TRANSACTION_STATUSES = [
  "draft",
  "ready",
  "billed",
  "paid",
  "completed",
  "canceled",
  "past_due",
] as const;

function transactionStatusLabel(t: (key: string) => string, status: string): string {
  return (KNOWN_TRANSACTION_STATUSES as readonly string[]).includes(status)
    ? t(`transactionStatus.${status}`)
    : status;
}

/** "Premium · 生效中 · renews on <date>" (or "cancels on <date>"): the facts a subscriber looks
 * for, from the local subscription row (which the webhook keeps current). */
function subscriptionHint(
  t: (key: string, values?: Record<string, string>) => string,
  subscription: SubscriptionRecord,
  locale: string,
): string {
  const parts = [
    `${t(`tierOption.${subscription.tier}`)}${subscription.byok ? ` · ${t("byok")}` : ""}`,
    statusLabel(t, subscription.status),
  ];
  const cancelOn = formatDate(subscription.scheduled_cancel_at, locale);
  const renewOn = formatDate(subscription.current_period_end, locale);
  if (cancelOn) {
    parts.push(t("cancelsOn", { date: cancelOn }));
  } else if (renewOn) {
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

function billingErrorMessage(err: unknown, tc: (key: string) => string): string {
  if (err instanceof SettingsApiError && err.code === "billing_unconfigured") {
    return tc("billingUnconfigured");
  }
  return tc("errorGeneric");
}
