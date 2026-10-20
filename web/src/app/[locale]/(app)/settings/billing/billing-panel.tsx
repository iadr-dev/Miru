"use client";

import {
  type QueryClient,
  type UseQueryResult,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type MutableRefObject, type ReactNode, useEffect, useRef, useState } from "react";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsLoading } from "@/components/settings/settings-states";
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
import { isBillableTransaction, summarizeTransaction } from "@/lib/billing-format";
import {
  hasReceipt,
  type PlanSelection,
  selectionFor,
  subscriptionNotice,
} from "@/lib/billing-state";
import { CHECKOUT_COMPLETED, loadPaddle, paddleLocale, setCheckoutListener } from "@/lib/paddle";
import type {
  BillingTransaction,
  PlanInfo,
  PlansResponse,
  SubscriptionRecord,
  SubscriptionResponse,
} from "@/types/settings";
import { NoticeRow, type PlanPicker, PlansBody, SubscriptionBody } from "./billing-subscription";
import type { BillingInterval, PayableTier } from "./plans-form";
import { TierComparison } from "./tier-comparison";

export function BillingPanel(): ReactNode {
  const t = useTranslations("settings.billing");
  const tc = useTranslations("settings.common");

  const locale = useLocale();
  const country = priceCountryFor(locale);
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
  // The open checkout's transaction, so a lost webhook can be recovered by asking Paddle directly.
  const transactionId = useRef<string | null>(null);
  const queryClient = useQueryClient();

  const checkoutMutation = useMutation({
    mutationFn: () =>
      openCheckoutOverlay({
        plansData: plansQuery.data,
        selection: { tier, interval, byok },
        locale,
        transactionId,
      }),
    onSuccess: () => setActionError(null),
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  const activation = useActivation(queryClient, transactionId);

  // Paddle's customer portal opens in a new tab so the user keeps their place in Miru. The tab is
  // opened inside the click (popup blockers allow that) and pointed at the portal link once the
  // server has created it; if the browser blocked the tab, the portal opens in this one instead.
  const portalMutation = useMutation({
    mutationFn: openPortalInTab,
    onError: (err) => setActionError(billingErrorMessage(err, tc)),
  });

  const plans = plansQuery.data?.plans ?? [];
  const subscription = subscriptionQuery.data?.subscription ?? null;

  // A subscriber's picker starts on the plan they are on.
  useSyncPickerToSubscription(plans, subscription, { setTier, setInterval, setByok });

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
    mutationFn: downloadReceipt,
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

  const notice = subscriptionNotice(subscription);
  const picker: PlanPicker = {
    tier,
    setTier,
    interval,
    setInterval,
    byok,
    setByok,
    actionError,
    locale,
  };

  return (
    <>
      {activation === "idle" ? null : (
        <p role="status" className="mb-4 text-[13px] text-[var(--text-muted)]">
          {activation === "waiting" ? t("activationWaiting") : t("activationDelayed")}
        </p>
      )}
      <SettingsSection title={t("subscriptionTitle")} className="mb-6">
        <SubscriptionBody
          failed={subscriptionQuery.isError}
          onRetry={() => void subscriptionQuery.refetch()}
          subscription={subscription}
          plans={plans}
          locale={locale}
          portalPending={portalMutation.isPending}
          onManage={() =>
            portalMutation.mutate({ tab: window.open("", "_blank"), purpose: "overview" })
          }
        />
        {notice ? (
          <NoticeRow
            notice={notice}
            subscription={subscription}
            locale={locale}
            portalPending={portalMutation.isPending}
            resumePending={resumeMutation.isPending}
            onUpdatePayment={() =>
              portalMutation.mutate({ tab: window.open("", "_blank"), purpose: "payment" })
            }
            onResume={() => resumeMutation.mutate()}
          />
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
        <PlansBody
          unconfigured={isBillingUnconfigured(plansQuery)}
          subscriptionFailed={subscriptionQuery.isError}
          subscription={subscription}
          plans={plans}
          picker={picker}
          changePending={changeMutation.isPending}
          onChange={() => changeMutation.mutate()}
          checkoutPending={checkoutMutation.isPending}
          onCheckout={() => checkoutMutation.mutate()}
        />
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

type Activation = "idle" | "waiting" | "delayed";

/** Paddle prices Taiwan separately (TWD), so a zh-TW UI asks for the Taiwan price. */
function priceCountryFor(locale: string): string | undefined {
  return locale === "zh-TW" ? "TW" : undefined;
}

/**
 * After Paddle reports a completed payment, the page waits for the webhook to create the
 * subscription: Paddle, not this browser event, is what grants the plan.
 */
function useActivation(
  queryClient: QueryClient,
  transactionId: MutableRefObject<string | null>,
): Activation {
  const [activation, setActivation] = useState<Activation>("idle");

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
      const found = await pollForSubscription(queryClient, transactionId, () => cancelled);
      if (found) {
        setActivation("idle");
      } else if (!cancelled) {
        setActivation("delayed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activation, queryClient, transactionId]);

  return activation;
}

type PickerSetters = {
  readonly setTier: (tier: PayableTier) => void;
  readonly setInterval: (interval: BillingInterval) => void;
  readonly setByok: (byok: boolean) => void;
};

/** Once, when the subscription is known, move the picker onto the plan it is on. */
function useSyncPickerToSubscription(
  plans: readonly PlanInfo[],
  subscription: SubscriptionRecord | null,
  { setTier, setInterval, setByok }: PickerSetters,
): void {
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
  }, [plans, subscription, setTier, setInterval, setByok]);
}

/** Billing is not set up on the server (no client token and no plans, or the API says so). */
function isBillingUnconfigured(plansQuery: UseQueryResult<PlansResponse>): boolean {
  if (plansQuery.isError && plansQuery.error instanceof SettingsApiError) {
    return plansQuery.error.code === "billing_unconfigured";
  }
  return !plansQuery.data?.client_token && (plansQuery.data?.plans.length ?? 0) === 0;
}

/** Open the Paddle checkout overlay for the picked plan. */
async function openCheckoutOverlay({
  plansData,
  selection,
  locale,
  transactionId,
}: {
  readonly plansData: PlansResponse | undefined;
  readonly selection: PlanSelection;
  readonly locale: string;
  readonly transactionId: MutableRefObject<string | null>;
}): Promise<void> {
  const token = plansData?.client_token;
  if (!token) {
    throw new CheckoutUnavailableError("no client token");
  }
  const [paddle, transaction] = await Promise.all([
    loadPaddle(token, plansData?.environment ?? "sandbox"),
    checkout(selection),
  ]);
  if (!paddle) {
    throw new CheckoutUnavailableError("paddle.js did not load");
  }
  transactionId.current = transaction.transaction_id;
  paddle.Checkout.open({
    transactionId: transaction.transaction_id,
    settings: { displayMode: "overlay", theme: "dark", locale: paddleLocale(locale) },
  });
}

/**
 * Wait for the webhook to create the subscription, polling the subscription read. Once enough
 * polls have passed the payment is confirmed with Paddle directly. Resolves true when a
 * subscription appeared (the dependent reads are refreshed), false when the polls ran out or
 * `isCancelled` turned true.
 */
async function pollForSubscription(
  queryClient: QueryClient,
  transactionId: MutableRefObject<string | null>,
  isCancelled: () => boolean,
): Promise<boolean> {
  for (let attempt = 0; attempt < ACTIVATION_POLLS && !isCancelled(); attempt += 1) {
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
      return true;
    }
  }
  return false;
}

async function openPortalInTab({
  tab,
  purpose,
}: {
  tab: Window | null;
  purpose: "overview" | "payment";
}): Promise<void> {
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
}

async function downloadReceipt(transactionId: string): Promise<void> {
  const url = await getInvoiceUrl(transactionId);
  if (!url) {
    throw new CheckoutUnavailableError("no invoice url");
  }
  downloadFile(url);
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
