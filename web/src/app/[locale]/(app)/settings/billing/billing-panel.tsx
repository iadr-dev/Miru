"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import { queryKeys } from "@/lib/api/query-keys";
import {
  changePlan,
  checkout,
  getBillingHistory,
  getInvoiceUrl,
  getSubscription,
  listPlans,
  openBillingPortal,
  resumeSubscription,
  SettingsApiError,
  syncCheckout,
} from "@/lib/api/settings";
import {
  formatDate,
  formatMinorAmount,
  isBillableTransaction,
  summarizeTransaction,
} from "@/lib/billing-format";
import {
  changeBlockReason,
  findPlan,
  hasReceipt,
  isLiveSubscription,
  selectionFor,
  subscriptionNotice,
} from "@/lib/billing-state";
import { CHECKOUT_COMPLETED, loadPaddle, paddleLocale, setCheckoutListener } from "@/lib/paddle";
import type {
  BillingTransaction,
  PlanInfo,
  SubscriptionRecord,
  SubscriptionResponse,
} from "@/types/settings";
import { type BillingInterval, type PayableTier, PlansForm } from "./plans-form";
import { TierComparison } from "./tier-comparison";

export function BillingPanel(): ReactNode {
  const t = useTranslations("settings.billing");
  const tc = useTranslations("settings.common");

  const locale = useLocale();
  // Paddle prices Taiwan separately (TWD), so a zh-TW UI asks for the Taiwan price.
  const country = locale === "zh-TW" ? "TW" : undefined;
  const plansQuery = useQuery({
    queryKey: queryKeys.settings("plans", country),
    queryFn: () => listPlans(country),
  });
  const subscriptionQuery = useQuery({
    queryKey: queryKeys.settings("subscription"),
    queryFn: getSubscription,
  });

  const [tier, setTier] = useState<PayableTier>("premium");
  const [interval, setInterval] = useState<BillingInterval>("monthly");
  const [byok, setByok] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  // After Paddle reports a completed payment, the page waits for the webhook to create the
  // subscription: Paddle, not this browser event, is what grants the plan.
  const [activation, setActivation] = useState<"idle" | "waiting" | "delayed">("idle");
  // The open checkout's transaction, so a lost webhook can be recovered by asking Paddle directly.
  const transactionId = useRef<string | null>(null);
  const queryClient = useQueryClient();

  const checkoutMutation = useMutation({
    mutationFn: async () => {
      const token = plansQuery.data?.client_token;
      if (!token) {
        throw new CheckoutUnavailableError("no client token");
      }
      const [paddle, transaction] = await Promise.all([
        loadPaddle(token, plansQuery.data?.environment ?? "sandbox"),
        checkout({ tier, interval, byok }),
      ]);
      if (!paddle) {
        throw new CheckoutUnavailableError("paddle.js did not load");
      }
      transactionId.current = transaction.transaction_id;
      paddle.Checkout.open({
        transactionId: transaction.transaction_id,
        settings: { displayMode: "overlay", theme: "dark", locale: paddleLocale(locale) },
      });
    },
    onSuccess: () => setActionError(null),
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  useEffect(() => {
    setCheckoutListener((event) => {
      if (event.name === CHECKOUT_COMPLETED) {
        setActivation("waiting");
      }
    });
    return () => setCheckoutListener(null);
  }, []);

  useEffect(() => {
    if (activation !== "waiting") {
      return;
    }
    let cancelled = false;
    void (async () => {
      for (let attempt = 0; attempt < ACTIVATION_POLLS && !cancelled; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, ACTIVATION_POLL_MS));
        if (attempt === SYNC_AFTER_POLLS && transactionId.current) {
          // The webhook is late (or lost): confirm the payment with Paddle ourselves.
          await syncCheckout(transactionId.current).catch(() => undefined);
        }
        await queryClient.invalidateQueries({ queryKey: queryKeys.settings("subscription") });
        const current = queryClient.getQueryData<{ subscription: unknown }>([
          "settings",
          "subscription",
        ]);
        if (current?.subscription) {
          await queryClient.invalidateQueries({ queryKey: queryKeys.settings("billing-history") });
          await queryClient.invalidateQueries({ queryKey: queryKeys.me });
          setActivation("idle");
          return;
        }
      }
      if (!cancelled) {
        setActivation("delayed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activation, queryClient]);

  // Paddle's customer portal opens in a new tab so the user keeps their place in Miru. The tab is
  // opened inside the click (popup blockers allow that) and pointed at the portal link once the
  // server has created it; if the browser blocked the tab, the portal opens in this one instead.
  const portalMutation = useMutation({
    mutationFn: async ({
      tab,
      purpose,
    }: {
      tab: Window | null;
      purpose: "overview" | "payment";
    }) => {
      try {
        const { portal_url } = await openBillingPortal(purpose);
        if (!portal_url) {
          throw new CheckoutUnavailableError("no portal url");
        }
        sendTab(tab, portal_url);
      } catch (err) {
        tab?.close();
        throw err;
      }
    },
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  const plans = plansQuery.data?.plans ?? [];
  const subscription = subscriptionQuery.data?.subscription ?? null;
  const live = isLiveSubscription(subscription);

  // A subscriber's picker starts on the plan they are on.
  const synced = useRef(false);
  useEffect(() => {
    if (synced.current || !subscription) {
      return;
    }
    const current = selectionFor(plans, subscription.price_id);
    if (current) {
      setTier(current.tier);
      setInterval(current.interval);
      setByok(current.byok);
      synced.current = true;
    }
  }, [plans, subscription]);

  const applySubscription = (data: SubscriptionResponse): void => {
    queryClient.setQueryData(["settings", "subscription"], data);
    void queryClient.invalidateQueries({ queryKey: queryKeys.me });
    void queryClient.invalidateQueries({ queryKey: queryKeys.settings("billing-history") });
    setActionError(null);
  };

  const changeMutation = useMutation({
    mutationFn: () => changePlan({ tier, interval, byok }),
    onSuccess: applySubscription,
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  const resumeMutation = useMutation({
    mutationFn: resumeSubscription,
    onSuccess: applySubscription,
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  // A receipt is a PDF download: fetch its link, then download it from this page. Opening a tab for
  // it would leave an empty tab behind once the browser turns the PDF into a download.
  const receiptMutation = useMutation({
    mutationFn: async (transactionId: string) => {
      const url = await getInvoiceUrl(transactionId);
      if (!url) {
        throw new CheckoutUnavailableError("no invoice url");
      }
      downloadFile(url);
    },
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  const historyQuery = useQuery({
    queryKey: queryKeys.settings("billing-history"),
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

  const selectedPlan = findPlan(plans, { tier, interval, byok });

  // A subscriber changes plan here (Paddle's portal only offers card updates and cancelling); a
  // second checkout would bill them twice, so the server refuses it too.
  const block = live && subscription ? changeBlockReason(subscription, selectedPlan) : null;
  const notice = subscriptionNotice(subscription);
  const planLabel = `${t(`tierOption.${tier}`)} · ${t(`intervalOption.${interval}`)}${byok ? ` · ${t("byok")}` : ""}`;
  const confirmText = t("confirmChangeText", {
    plan: selectedPlan?.price
      ? `${planLabel} (${formatMinorAmount(selectedPlan.price.amount, selectedPlan.price.currency_code, locale)})`
      : planLabel,
  });

  const unconfigured =
    plansQuery.isError && plansQuery.error instanceof SettingsApiError
      ? plansQuery.error.code === "billing_unconfigured"
      : !plansQuery.data?.client_token && (plansQuery.data?.plans.length ?? 0) === 0;

  return (
    <>
      {activation === "idle" ? null : (
        <p role="status" className="mb-4 text-[13px] text-[var(--text-muted)]">
          {activation === "waiting" ? t("activationWaiting") : t("activationDelayed")}
        </p>
      )}
      <SettingsSection title={t("subscriptionTitle")} className="mb-6">
        {subscriptionQuery.isError ? (
          <SettingsError
            message={tc("errorLoad")}
            onRetry={() => void subscriptionQuery.refetch()}
          />
        ) : subscription && live ? (
          <SettingsRow
            label={t("currentPlan")}
            hint={subscriptionHint(t, subscription, plans, locale)}
          >
            <Button
              variant="outline"
              size="sm"
              disabled={portalMutation.isPending}
              onClick={() =>
                portalMutation.mutate({ tab: window.open("", "_blank"), purpose: "overview" })
              }
            >
              {t("managePlan")}
            </Button>
          </SettingsRow>
        ) : (
          <p className="text-[13px] text-[var(--text-muted)]">{t("noSubscription")}</p>
        )}
        {notice ? (
          <div className="flex items-center justify-between gap-3 border-[var(--border)] border-t py-3">
            <p role="status" className="text-[13px] text-[var(--warn)]">
              {notice === "pastDue"
                ? t("pastDueNotice")
                : notice === "paused"
                  ? t("pausedNotice")
                  : t("cancelScheduledNotice", {
                      date: formatDate(subscription?.scheduled_cancel_at, locale) ?? "",
                    })}
            </p>
            {notice === "pastDue" ? (
              <Button
                variant="outline"
                size="sm"
                disabled={portalMutation.isPending}
                onClick={() =>
                  portalMutation.mutate({ tab: window.open("", "_blank"), purpose: "payment" })
                }
              >
                {t("updatePayment")}
              </Button>
            ) : (
              <Button
                variant="outline"
                size="sm"
                disabled={resumeMutation.isPending}
                onClick={() => resumeMutation.mutate()}
              >
                {t("resumeSubscription")}
              </Button>
            )}
          </div>
        ) : null}
        <SettingsRow label={t("byokActive")}>
          <span className="text-[13px] text-[var(--text)]">
            {subscriptionQuery.data?.byok_active ? tc("yes") : tc("no")}
          </span>
        </SettingsRow>
      </SettingsSection>

      <SettingsSection title={t("compare.title")} className="mb-6">
        <TierComparison
          tiers={plansQuery.data?.tiers ?? []}
          current={subscriptionQuery.data?.subscription?.tier ?? "free"}
        />
      </SettingsSection>

      <SettingsSection title={t("plansTitle")} className="mb-6">
        {unconfigured ? (
          <p className="text-[13px] text-[var(--text-muted)]">{t("unconfigured")}</p>
        ) : subscriptionQuery.isError ? null : live ? (
          <PlansForm
            tier={tier}
            setTier={setTier}
            interval={interval}
            setInterval={setInterval}
            byok={byok}
            setByok={setByok}
            actionError={actionError}
            pending={changeMutation.isPending}
            onSubmit={() => changeMutation.mutate()}
            submitLabel={t("changePlan")}
            blockedNote={block ? t(`changeBlocked.${block}`) : null}
            confirmText={confirmText}
            plan={selectedPlan}
            locale={locale}
          />
        ) : (
          <PlansForm
            tier={tier}
            setTier={setTier}
            interval={interval}
            setInterval={setInterval}
            byok={byok}
            setByok={setByok}
            actionError={actionError}
            pending={checkoutMutation.isPending}
            onSubmit={() => checkoutMutation.mutate()}
            submitLabel={t("checkout")}
            plan={selectedPlan}
            locale={locale}
          />
        )}
      </SettingsSection>

      <SettingsSection title={t("historyTitle")}>
        <BillingHistorySection
          query={historyQuery}
          locale={locale}
          onReceipt={(id) => receiptMutation.mutate(id)}
        />
      </SettingsSection>
    </>
  );
}

function BillingHistorySection({
  query,
  locale,
  onReceipt,
}: Readonly<{
  query: ReturnType<typeof useQuery<BillingTransaction[]>>;
  locale: string;
  onReceipt: (transactionId: string) => void;
}>): ReactNode {
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
  const transactions = (query.data ?? []).filter(isBillableTransaction);
  if (transactions.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("noHistory")}</p>;
  }
  return <HistoryList transactions={transactions} locale={locale} onReceipt={onReceipt} />;
}

function HistoryList({
  transactions,
  locale,
  onReceipt,
}: Readonly<{
  transactions: BillingTransaction[];
  locale: string;
  onReceipt: (transactionId: string) => void;
}>): ReactNode {
  const t = useTranslations("settings.billing");
  return (
    <ul className="divide-y divide-[var(--border)]">
      {transactions.map((tx) => {
        const row = summarizeTransaction(tx, locale);
        return (
          <li key={row.id} className="flex items-center justify-between gap-3 py-2.5 text-[13px]">
            <span className="text-[var(--text-muted)] tabular-nums">{row.date ?? "—"}</span>
            <span className="font-medium text-[var(--text)] tabular-nums">{row.amount ?? "—"}</span>
            <span className="text-[var(--text-muted)]">
              {transactionStatusLabel(t, row.status)}
            </span>
            {hasReceipt(row.status) ? (
              <button
                type="button"
                className="text-[var(--accent)] underline"
                onClick={() => onReceipt(row.id)}
              >
                {t("receipt")}
              </button>
            ) : (
              <span />
            )}
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

/** The checkout overlay can't open: no client token configured, or Paddle.js didn't load. */
class CheckoutUnavailableError extends Error {}

/** Point a tab opened inside a click at its destination, or fall back to this tab if the browser
 * blocked the new one. `opener` is cleared so the destination can't reach back into Miru. */
function sendTab(tab: Window | null, url: string): void {
  if (tab) {
    tab.opener = null;
    tab.location.href = url;
  } else {
    window.location.assign(url);
  }
}

/** Start a download of `url` from this page. Paddle serves the invoice as an attachment, so the
 * browser saves it and Miru stays exactly where it is. */
function downloadFile(url: string): void {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  link.rel = "noopener noreferrer";
  document.body.append(link);
  link.click();
  link.remove();
}

const ACTIVATION_POLLS = 10;
const SYNC_AFTER_POLLS = 2;
const ACTIVATION_POLL_MS = 2000;

function billingErrorMessage(err: unknown, tc: (key: string) => string): string {
  if (err instanceof CheckoutUnavailableError) {
    return tc("checkoutUnavailable");
  }
  if (err instanceof SettingsApiError && err.code === "billing_unconfigured") {
    return tc("billingUnconfigured");
  }
  if (err instanceof SettingsApiError && err.code === "same_plan") {
    return tc("billingSamePlan");
  }
  if (err instanceof SettingsApiError && err.code === "cancel_scheduled") {
    return tc("billingCancelScheduled");
  }
  return tc("errorGeneric");
}
