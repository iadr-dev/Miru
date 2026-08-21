"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
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
import { BILLING_INTERVALS, type BillingTransaction, PAYABLE_TIERS } from "@/types/settings";

export function BillingPanel(): ReactNode {
  const t = useTranslations("settings.billing");
  const tc = useTranslations("settings.common");

  const plansQuery = useQuery({ queryKey: ["settings", "plans"], queryFn: listPlans });
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
            hint={statusLabel(t, subscriptionQuery.data.subscription.status)}
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
          />
        )}
      </SettingsSection>

      <SettingsSection title={t("historyTitle")}>
        <BillingHistorySection query={historyQuery} />
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
}: {
  query: ReturnType<typeof useQuery<BillingTransaction[]>>;
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
  return <HistoryList transactions={transactions} />;
}

function HistoryList({ transactions }: { transactions: BillingTransaction[] }): ReactNode {
  return (
    <ul className="space-y-2 py-1">
      {transactions.map((tx, i) => {
        const idLike = tx.id ?? tx.transaction_id;
        const key = typeof idLike === "string" ? idLike : `tx-${i}`;
        return (
          <li key={key} className="rounded-[var(--radius-input)] bg-[var(--surface-2)] p-2">
            <pre className="overflow-x-auto whitespace-pre-wrap text-[11px] text-[var(--text-muted)]">
              {JSON.stringify(tx, null, 2)}
            </pre>
          </li>
        );
      })}
    </ul>
  );
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
