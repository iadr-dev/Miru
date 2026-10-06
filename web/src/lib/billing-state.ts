/** Decisions the billing page makes about a subscription, kept pure so they can be tested without
 * rendering anything. Paddle's webhooks keep the local subscription row current; these only read it. */
import type { PlanInfo, SubscriptionRecord } from "@/types/settings";

export type PlanSelection = Pick<PlanInfo, "tier" | "interval" | "byok">;

/** A subscription the user can still manage: anything but one that has ended. */
export function isLiveSubscription(sub: SubscriptionRecord | null | undefined): boolean {
  return sub != null && sub.status !== "canceled";
}

export type SubscriptionNotice = "pastDue" | "paused" | "cancelScheduled" | null;

/** The one thing a subscriber should be told about right now, if anything. A failed payment
 * outranks a pause, which outranks a scheduled cancel: each needs action to keep or restore
 * the plan, most urgent first. */
export function subscriptionNotice(sub: SubscriptionRecord | null | undefined): SubscriptionNotice {
  if (!isLiveSubscription(sub) || sub == null) {
    return null;
  }
  if (sub.status === "past_due") {
    return "pastDue";
  }
  if (sub.status === "paused") {
    return "paused";
  }
  return sub.scheduled_cancel_at ? "cancelScheduled" : null;
}

export function findPlan(
  plans: readonly PlanInfo[],
  selection: PlanSelection,
): PlanInfo | undefined {
  return plans.find(
    (p) =>
      p.tier === selection.tier && p.interval === selection.interval && p.byok === selection.byok,
  );
}

/** The tier / interval / BYOK choice that a price id stands for (to preselect the picker). */
export function selectionFor(
  plans: readonly PlanInfo[],
  priceId: string | null | undefined,
): PlanSelection | null {
  const plan = plans.find((p) => p.price_id === priceId);
  return plan ? { tier: plan.tier, interval: plan.interval, byok: plan.byok } : null;
}

export type ChangeBlock = "paused" | "cancelScheduled" | "samePlan" | "unavailable";

/** Why a plan change can't be made right now, or null if it can. */
export function changeBlockReason(
  sub: SubscriptionRecord,
  target: PlanInfo | undefined,
): ChangeBlock | null {
  if (sub.status === "paused") {
    return "paused";
  }
  if (sub.scheduled_cancel_at) {
    return "cancelScheduled";
  }
  if (!target) {
    return "unavailable";
  }
  return target.price_id === sub.price_id ? "samePlan" : null;
}

/** Receipts exist only for transactions that were actually charged. */
export function hasReceipt(status: string | null): boolean {
  return status === "completed" || status === "paid" || status === "billed";
}
