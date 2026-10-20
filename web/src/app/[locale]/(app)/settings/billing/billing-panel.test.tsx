// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BillingPanel } from "@/app/[locale]/(app)/settings/billing/billing-panel";
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
import { CHECKOUT_COMPLETED, loadPaddle, setCheckoutListener } from "@/lib/paddle";
import { messages, renderWithIntl } from "@/test/render";
import type { BillingTransaction, PlanInfo, SubscriptionRecord } from "@/types/settings";

vi.mock("@/lib/api/settings", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/settings")>();
  return {
    ...actual,
    changePlan: vi.fn(),
    checkout: vi.fn(),
    getBillingHistory: vi.fn(),
    getInvoiceUrl: vi.fn(),
    getSubscription: vi.fn(),
    listPlans: vi.fn(),
    openBillingPortal: vi.fn(),
    resumeSubscription: vi.fn(),
    syncCheckout: vi.fn(),
  };
});
vi.mock("@/lib/paddle", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/paddle")>();
  return { ...actual, loadPaddle: vi.fn(), setCheckoutListener: vi.fn() };
});

const t = messages.settings.billing;
const tc = messages.settings.common;

const PLANS: PlanInfo[] = [
  {
    price_id: "pri_p_m",
    tier: "premium",
    interval: "monthly",
    byok: false,
    price: { amount: "29900", currency_code: "TWD" },
  },
  {
    price_id: "pri_pro_m",
    tier: "pro",
    interval: "monthly",
    byok: false,
    price: { amount: "59900", currency_code: "TWD" },
  },
];

const SUB: SubscriptionRecord = {
  id: "s1",
  user_id: "u1",
  paddle_subscription_id: "sub_1",
  paddle_customer_id: "ctm_1",
  status: "active",
  price_id: "pri_p_m",
  tier: "premium",
  byok: false,
  current_period_start: "2026-01-01T00:00:00Z",
  current_period_end: "2026-02-01T00:00:00Z",
  scheduled_cancel_at: null,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const TX: BillingTransaction[] = [
  {
    id: "txn_1",
    status: "completed",
    created_at: "2026-01-01T00:00:00Z",
    billed_at: "2026-01-01T00:00:00Z",
    currency_code: "TWD",
    total: "29900",
  },
  {
    id: "txn_2",
    status: "weird_status",
    created_at: "2026-01-02T00:00:00Z",
    billed_at: null,
    currency_code: "TWD",
    total: "100",
  },
  {
    id: "txn_3",
    status: "draft",
    created_at: null,
    billed_at: null,
    currency_code: null,
    total: null,
  },
];

type Setup = {
  readonly plans?: "ok" | "unconfigured" | "error" | "empty" | "loading";
  readonly subscription?: SubscriptionRecord | null | "error";
  readonly byokActive?: boolean;
  readonly history?: BillingTransaction[] | "error" | "unconfigured";
};

function arrange(s: Setup): void {
  const plans = s.plans ?? "ok";
  if (plans === "loading") {
    vi.mocked(listPlans).mockReturnValue(new Promise(() => undefined));
  } else if (plans === "unconfigured") {
    vi.mocked(listPlans).mockRejectedValue(
      new SettingsApiError("billing_unconfigured", "x", 503, false),
    );
  } else if (plans === "error") {
    vi.mocked(listPlans).mockRejectedValue(new Error("boom"));
  } else if (plans === "empty") {
    vi.mocked(listPlans).mockResolvedValue({
      client_token: null,
      environment: "sandbox",
      plans: [],
      tiers: [],
    });
  } else {
    vi.mocked(listPlans).mockResolvedValue({
      client_token: "tok",
      environment: "sandbox",
      plans: PLANS,
      tiers: [],
    });
  }
  const sub = s.subscription ?? null;
  if (sub === "error") {
    vi.mocked(getSubscription).mockRejectedValue(new Error("boom"));
  } else {
    vi.mocked(getSubscription).mockResolvedValue({
      subscription: sub,
      byok_active: s.byokActive ?? false,
    });
  }
  const history = s.history ?? [];
  if (history === "error") {
    vi.mocked(getBillingHistory).mockRejectedValue(new Error("boom"));
  } else if (history === "unconfigured") {
    vi.mocked(getBillingHistory).mockRejectedValue(
      new SettingsApiError("billing_unconfigured", "x", 503, false),
    );
  } else {
    vi.mocked(getBillingHistory).mockResolvedValue(history);
  }
}

async function settle(rounds = 5): Promise<void> {
  for (let i = 0; i < rounds; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function html(s: Setup): Promise<string> {
  arrange(s);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithIntl(
    <QueryClientProvider client={client}>
      <BillingPanel />
    </QueryClientProvider>,
  );
  await settle();
  return view.container.innerHTML;
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("BillingPanel states", () => {
  it("shows the loading skeleton while plans load", async () => {
    expect(await html({ plans: "loading" })).toMatchSnapshot();
  });

  it("no subscription: checkout form, plain history", async () => {
    expect(await html({})).toMatchSnapshot();
  });

  it("no subscription with a BYOK flag", async () => {
    expect(await html({ byokActive: true })).toMatchSnapshot();
  });

  it("billing not configured hides the plans form", async () => {
    expect(await html({ plans: "unconfigured", history: "unconfigured" })).toMatchSnapshot();
  });

  it("empty plans without a client token counts as not configured", async () => {
    expect(await html({ plans: "empty" })).toMatchSnapshot();
  });

  it("a generic plans error still shows the checkout form", async () => {
    expect(await html({ plans: "error", history: "error" })).toMatchSnapshot();
  });

  it("subscription load error shows the retry state and no plans form", async () => {
    expect(await html({ subscription: "error" })).toMatchSnapshot();
  });

  it("live subscription: current plan row and the change-plan form", async () => {
    expect(await html({ subscription: SUB, history: TX })).toMatchSnapshot();
  });

  it("live subscription with BYOK and an unknown price", async () => {
    expect(
      await html({ subscription: { ...SUB, byok: true, price_id: "pri_other" } }),
    ).toMatchSnapshot();
  });

  it("renewal date is announced for active, cancel date when scheduled", async () => {
    expect(
      await html({ subscription: { ...SUB, scheduled_cancel_at: "2026-03-01T00:00:00Z" } }),
    ).toMatchSnapshot();
  });

  it("past-due notice with an update-payment button, and no renewal date", async () => {
    expect(await html({ subscription: { ...SUB, status: "past_due" } })).toMatchSnapshot();
  });

  it("paused notice with a resume button", async () => {
    expect(await html({ subscription: { ...SUB, status: "paused" } })).toMatchSnapshot();
  });

  it("trialing and unknown statuses are labelled", async () => {
    expect(await html({ subscription: { ...SUB, status: "trialing" } })).toMatchSnapshot();
    cleanup();
    expect(await html({ subscription: { ...SUB, status: "mystery" } })).toMatchSnapshot();
  });

  it("a canceled subscription is treated as none", async () => {
    expect(await html({ subscription: { ...SUB, status: "canceled" } })).toMatchSnapshot();
  });

  it("history list with receipts only for charged transactions", async () => {
    const out = await html({ history: TX });
    expect(out).toContain(t.receipt);
    expect(screen.getAllByText(t.receipt)).toHaveLength(1);
    expect(out).toContain("weird_status");
  });
});

describe("BillingPanel actions", () => {
  it("asks for confirmation before changing plan, then submits and shows the new plan", async () => {
    arrange({ subscription: SUB });
    vi.mocked(changePlan).mockResolvedValue({
      subscription: { ...SUB, price_id: "pri_pro_m", tier: "pro" },
      byok_active: false,
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.change(screen.getByLabelText(t.tier), { target: { value: "pro" } });
    fireEvent.click(screen.getByRole("button", { name: t.changePlan }));
    expect(screen.getByRole("button", { name: t.confirmChange })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: t.confirmChange }));
    await settle();
    expect(changePlan).toHaveBeenCalledWith({ tier: "pro", interval: "monthly", byok: false });
  });

  it("shows the same-plan block note and disables the change button", async () => {
    await html({ subscription: SUB });
    expect(screen.getByText(t.changeBlocked.samePlan)).toBeTruthy();
    expect((screen.getByRole("button", { name: t.changePlan }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("surfaces a change-plan error code as its message", async () => {
    arrange({ subscription: SUB });
    vi.mocked(changePlan).mockRejectedValue(
      new SettingsApiError("cancel_scheduled", "x", 409, false),
    );
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.change(screen.getByLabelText(t.tier), { target: { value: "pro" } });
    fireEvent.click(screen.getByRole("button", { name: t.changePlan }));
    fireEvent.click(screen.getByRole("button", { name: t.confirmChange }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(tc.billingCancelScheduled);
  });

  it("resumes a paused subscription", async () => {
    arrange({ subscription: { ...SUB, status: "paused" } });
    vi.mocked(resumeSubscription).mockResolvedValue({ subscription: SUB, byok_active: false });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.resumeSubscription }));
    await settle();
    expect(resumeSubscription).toHaveBeenCalledTimes(1);
  });

  it("opens the billing portal in a tab for manage and for updating payment", async () => {
    arrange({ subscription: { ...SUB, status: "past_due" } });
    vi.mocked(openBillingPortal).mockResolvedValue({ portal_url: "https://portal.test/x" });
    const tab = { location: { href: "" }, opener: "x", close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.updatePayment }));
    await settle();
    expect(openBillingPortal).toHaveBeenCalledWith("payment");
    expect(tab.location.href).toBe("https://portal.test/x");
    expect(tab.opener).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: t.managePlan }));
    await settle();
    expect(openBillingPortal).toHaveBeenLastCalledWith("overview");
    open.mockRestore();
  });

  it("closes the tab and shows an error when the portal link is missing", async () => {
    arrange({ subscription: SUB });
    vi.mocked(openBillingPortal).mockResolvedValue({ portal_url: null });
    const tab = { location: { href: "" }, opener: "x", close: vi.fn() };
    const open = vi.spyOn(window, "open").mockReturnValue(tab as never);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.managePlan }));
    await settle();
    expect(tab.close).toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toBe(tc.checkoutUnavailable);
    open.mockRestore();
  });

  it("downloads a receipt through a temporary link", async () => {
    arrange({ history: TX });
    vi.mocked(getInvoiceUrl).mockResolvedValue("https://files.test/inv.pdf");
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByText(t.receipt));
    await settle();
    expect(getInvoiceUrl).toHaveBeenCalledWith("txn_1");
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });

  it("opens the Paddle overlay for a checkout", async () => {
    arrange({});
    const openCheckout = vi.fn();
    vi.mocked(loadPaddle).mockResolvedValue({ Checkout: { open: openCheckout } } as never);
    vi.mocked(checkout).mockResolvedValue({ transaction_id: "txn_9", checkout_url: null });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.checkout }));
    await settle();
    expect(checkout).toHaveBeenCalledWith({ tier: "premium", interval: "monthly", byok: false });
    expect(openCheckout).toHaveBeenCalledWith({
      transactionId: "txn_9",
      settings: { displayMode: "overlay", theme: "dark", locale: expect.any(String) },
    });
  });

  it("shows checkout-unavailable when paddle.js did not load", async () => {
    arrange({});
    vi.mocked(loadPaddle).mockResolvedValue(null as never);
    vi.mocked(checkout).mockResolvedValue({ transaction_id: "txn_9", checkout_url: null });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.checkout }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(tc.checkoutUnavailable);
  });

  it.each([
    ["billing_unconfigured", tc.billingUnconfigured],
    ["same_plan", tc.billingSamePlan],
    ["other", tc.errorGeneric],
  ])("maps the checkout error code %s", async (code, message) => {
    arrange({});
    vi.mocked(loadPaddle).mockResolvedValue({ Checkout: { open: vi.fn() } } as never);
    vi.mocked(checkout).mockRejectedValue(new SettingsApiError(code, "x", 400, false));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.checkout }));
    await settle();
    expect(screen.getByRole("alert").textContent).toBe(message);
  });
});

describe("BillingPanel activation after payment", () => {
  async function completePayment(): Promise<void> {
    arrange({});
    vi.mocked(loadPaddle).mockResolvedValue({ Checkout: { open: vi.fn() } } as never);
    vi.mocked(checkout).mockResolvedValue({ transaction_id: "txn_7", checkout_url: null });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <BillingPanel />
      </QueryClientProvider>,
    );
    await settle();
    fireEvent.click(screen.getByRole("button", { name: t.checkout }));
    await settle();
    vi.useFakeTimers();
    const listener = vi.mocked(setCheckoutListener).mock.calls.at(-1)?.[0];
    if (!listener) throw new Error("no listener");
    act(() => {
      listener({ name: CHECKOUT_COMPLETED } as never);
    });
  }

  it("waits, syncs with Paddle after two polls, then reports a delay", async () => {
    vi.mocked(syncCheckout).mockResolvedValue(undefined as never);
    await completePayment();
    expect(screen.getByRole("status").textContent).toBe(t.activationWaiting);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000 * 2);
    });
    expect(syncCheckout).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(syncCheckout).toHaveBeenCalledWith("txn_7");
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000 * 10);
    });
    expect(screen.getByRole("status").textContent).toBe(t.activationDelayed);
  });

  it("returns to idle once the subscription shows up", async () => {
    vi.mocked(syncCheckout).mockResolvedValue(undefined as never);
    await completePayment();
    vi.mocked(getSubscription).mockResolvedValue({ subscription: SUB, byok_active: false });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.queryByText(t.activationWaiting)).toBeNull();
    expect(screen.queryByText(t.activationDelayed)).toBeNull();
  });
});
