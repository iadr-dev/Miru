import { describe, expect, it } from "vitest";
import type { PlanInfo, SubscriptionRecord } from "@/types/settings";
import {
  changeBlockReason,
  findPlan,
  hasReceipt,
  isLiveSubscription,
  selectionFor,
  subscriptionNotice,
} from "./billing-state";

const PLANS: PlanInfo[] = [
  { price_id: "pri_pm", tier: "premium", interval: "monthly", byok: false, price: null },
  { price_id: "pri_pa", tier: "premium", interval: "annual", byok: false, price: null },
  { price_id: "pri_pb", tier: "premium", interval: "monthly", byok: true, price: null },
  { price_id: "pri_xm", tier: "pro", interval: "monthly", byok: false, price: null },
];

function sub(over: Partial<SubscriptionRecord> = {}): SubscriptionRecord {
  return {
    id: "s1",
    user_id: "u1",
    paddle_subscription_id: "sub_1",
    paddle_customer_id: "ctm_1",
    status: "active",
    price_id: "pri_pm",
    tier: "premium",
    byok: false,
    current_period_start: "2026-10-06T00:00:00Z",
    current_period_end: "2026-11-06T00:00:00Z",
    scheduled_cancel_at: null,
    created_at: "2026-10-06T00:00:00Z",
    updated_at: "2026-10-06T00:00:00Z",
    ...over,
  };
}

describe("isLiveSubscription", () => {
  it("is false only for nothing or an ended subscription", () => {
    expect(isLiveSubscription(null)).toBe(false);
    expect(isLiveSubscription(sub({ status: "canceled" }))).toBe(false);
    for (const status of ["active", "trialing", "past_due", "paused"]) {
      expect(isLiveSubscription(sub({ status }))).toBe(true);
    }
  });
});

describe("subscriptionNotice", () => {
  it("says nothing for a healthy subscription", () => {
    expect(subscriptionNotice(sub())).toBeNull();
    expect(subscriptionNotice(null)).toBeNull();
    expect(subscriptionNotice(sub({ status: "canceled" }))).toBeNull();
  });

  it("flags a scheduled cancel and a failed payment, the latter first", () => {
    const canceling = sub({ scheduled_cancel_at: "2026-11-06T00:00:00Z" });
    expect(subscriptionNotice(canceling)).toBe("cancelScheduled");
    expect(subscriptionNotice(sub({ status: "past_due" }))).toBe("pastDue");
    expect(
      subscriptionNotice(sub({ status: "past_due", scheduled_cancel_at: "2026-11-06T00:00:00Z" })),
    ).toBe("pastDue");
  });
});

describe("plan lookup", () => {
  it("finds the plan for a selection and the selection for a price", () => {
    expect(findPlan(PLANS, { tier: "premium", interval: "annual", byok: false })?.price_id).toBe(
      "pri_pa",
    );
    expect(findPlan(PLANS, { tier: "pro", interval: "annual", byok: true })).toBeUndefined();
    expect(selectionFor(PLANS, "pri_pb")).toEqual({
      tier: "premium",
      interval: "monthly",
      byok: true,
    });
    expect(selectionFor(PLANS, "pri_unknown")).toBeNull();
  });
});

describe("changeBlockReason", () => {
  const pro = PLANS[3];

  it("allows a different, available plan", () => {
    expect(changeBlockReason(sub(), pro)).toBeNull();
  });

  it("blocks the current plan, a missing plan, and any change while a cancel is scheduled", () => {
    expect(changeBlockReason(sub(), PLANS[0])).toBe("samePlan");
    expect(changeBlockReason(sub(), undefined)).toBe("unavailable");
    expect(changeBlockReason(sub({ scheduled_cancel_at: "2026-11-06T00:00:00Z" }), pro)).toBe(
      "cancelScheduled",
    );
  });
});

describe("hasReceipt", () => {
  it("is true only for charged transactions", () => {
    for (const status of ["completed", "paid", "billed"]) {
      expect(hasReceipt(status)).toBe(true);
    }
    for (const status of ["draft", "ready", "canceled", "past_due", null]) {
      expect(hasReceipt(status)).toBe(false);
    }
  });
});
