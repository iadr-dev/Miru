// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureLock } from "@/components/ui/feature-lock";
import { fetchMe, UnauthenticatedError } from "@/lib/api/account";
import { messages, renderWithIntl } from "@/test/render";
import type { MeResponse } from "@/types/account";

vi.mock("@/lib/api/account", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/account")>()),
  fetchMe: vi.fn(),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({
    href,
    children,
    ...rest
  }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...rest}>
      {children as ReactNode}
    </a>
  ),
}));

const copy = messages.featureLock;

// Invented account.
const FREE: MeResponse = {
  user: {
    id: "u1",
    display_name: "Ada",
    personality_id: "steady",
    tier: "free",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  entitlements: {
    tier: "free",
    chat_limit_per_day: 3,
    finmind_class: "free",
    byok_active: false,
    features: [],
  },
};

function renderLock(ui: ReactNode): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithIntl(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.mocked(fetchMe).mockResolvedValue(FREE);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("FeatureLock", () => {
  it("row: count, benefit and an upgrade link to billing for a signed-in caller", async () => {
    renderLock(<FeatureLock message="還有 3 家公司" benefit="Pro 可查看全部" />);
    const cta = await screen.findByRole("link", { name: copy.upgrade });
    expect(cta.getAttribute("href")).toBe("/settings/billing");
    expect(screen.getByText("還有 3 家公司")).toBeTruthy();
    expect(screen.queryByRole("button", { name: copy.signIn })).toBeNull();
  });

  it("row: an anonymous caller gets the sign-in flow instead of the billing link", async () => {
    vi.mocked(fetchMe).mockRejectedValue(new UnauthenticatedError());
    renderLock(<FeatureLock message="還有 3 家公司" />);
    const cta = await screen.findByRole("button", { name: copy.signIn });
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(cta);
    expect(await screen.findByRole("dialog")).toBeTruthy();
  });

  it("renders only the caller's copy: nothing blurred, no images, no extra text", async () => {
    renderLock(<FeatureLock message="還有 3 家公司" benefit="Pro 可查看全部" />);
    await screen.findByRole("link", { name: copy.upgrade });
    const container = document.body;
    const row = container.querySelector("[data-feature-lock]");
    expect(row?.textContent).toBe(`還有 3 家公司 · Pro 可查看全部${copy.upgrade}`);
    expect(container.querySelector("img, canvas, [class*='blur']")).toBeNull();
  });

  it("chip: a single 44px-high pill linking to billing, label only", async () => {
    renderLock(<FeatureLock variant="chip" message="更多題材・Pro" />);
    const chip = await screen.findByRole("link", { name: "更多題材・Pro" });
    expect(chip.getAttribute("href")).toBe("/settings/billing");
    expect(chip.className).toContain("min-h-11");
  });

  it("icon: a labelled lock control, 44px square", async () => {
    renderLock(<FeatureLock variant="icon" message="來源資料為 Pro 功能" />);
    const icon = await screen.findByRole("link", { name: "來源資料為 Pro 功能" });
    expect(icon.className).toContain("size-11");
  });

  it("chip: anonymous gets a sign-in button", async () => {
    vi.mocked(fetchMe).mockRejectedValue(new UnauthenticatedError());
    renderLock(<FeatureLock variant="chip" message="更多題材・Pro" />);
    await waitFor(() => expect(screen.getByRole("button", { name: "更多題材・Pro" })).toBeTruthy());
  });
});
