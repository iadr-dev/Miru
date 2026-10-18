// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeHeatPanel } from "@/components/market/theme-heat-panel";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { getThemeMembers, getThemes } from "@/lib/api/theme";
import { messages, renderWithIntl } from "@/test/render";
import type { ThemeMember, ThemeSummary } from "@/types/theme";

vi.mock("@/lib/api/theme", () => ({ getThemes: vi.fn(), getThemeMembers: vi.fn() }));
vi.mock("@/hooks/use-symbol-quotes", () => ({ useSymbolQuotes: vi.fn() }));
vi.mock("@/hooks/use-is-anonymous", () => ({ useIsAnonymous: () => false }));
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

const copy = messages.themes.tab;

// Invented themes and companies only.
function theme(key: string, name: string, description: string): ThemeSummary {
  return {
    key,
    name_zh: name,
    name_en: "",
    kind: "concept",
    status: "active",
    member_count: 20,
    description,
    related_keys: [],
  };
}

function members(codes: string[]): ThemeMember[] {
  return codes.map((symbol) => ({
    symbol,
    market: "TW",
    sub_industry: "",
    confidence: 1,
    source: "seed",
    position: null,
  }));
}

/** Daily change by code; a code that is absent has no quote. */
const CHANGES: Record<string, number> = { "1001": 2, "1003": 4, "3001": -1.5 };

const LASER = theme("laser-module", "Laser Module", "Modules that steer light.");
const QUIET = theme("quiet-theme", "Quiet Theme", "No quotes today.");
const THEMES = [LASER, QUIET];

function renderPanel(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithIntl(
    <QueryClientProvider client={client}>
      <ThemeHeatPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getThemes).mockResolvedValue({
    theme_count: THEMES.length,
    themes: THEMES,
    concept_locked: false,
  });
  vi.mocked(getThemeMembers).mockImplementation(async (key) => ({
    theme_key: key,
    found: true,
    name_zh: key,
    name_en: "",
    kind: "concept",
    member_count: 20,
    description: "",
    related_keys: [],
    // Three members, one of which has no quote; a long theme is cut to the sample.
    locked: false,
    hidden_count: 0,
    members:
      key === "laser-module"
        ? members(["1001", "1002", "1003", ...Array.from({ length: 17 }, (_, i) => `${9000 + i}`)])
        : members(["4001", "4002"]),
    message: "",
  }));
  vi.mocked(useSymbolQuotes).mockImplementation((codes) => ({
    quotes: codes.map((code) => ({
      code,
      name: null,
      industryCategory: null,
      price: 10,
      changePct: CHANGES[code] ?? null,
      sparkline: [],
      volume: null,
    })),
    isLoading: false,
    isError: false,
  }));
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

async function cardFor(name: string): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name });
  const card = heading.closest("a");
  if (!card) throw new Error("card link missing");
  return card;
}

describe("ThemeHeatPanel", () => {
  it("links each concept theme card to its theme page and clamps the description", async () => {
    renderPanel();
    const card = await cardFor("Laser Module");
    expect(card.getAttribute("href")).toBe("/themes/laser-module");
    const description = within(card).getByText("Modules that steer light.");
    expect(description.className).toContain("line-clamp-2");
    expect(within(card).getByText(copy.members.replace("{count}", "20"))).toBeTruthy();
  });

  it("averages only the members that have a quote, from the first twelve", async () => {
    renderPanel();
    const card = within(await cardFor("Laser Module"));
    // 1001 (+2) and 1003 (+4) are quoted, 1002 is not: (2 + 4) / 2 = 3.00%.
    await waitFor(() => expect(card.getByRole("img", { name: /3\.00%/ })).toBeTruthy());
    expect(card.getByText(copy.avgSample.replace("{count}", "2"))).toBeTruthy();
    const asked = vi.mocked(useSymbolQuotes).mock.calls.map(([codes]) => codes.length);
    expect(Math.max(...asked)).toBeLessThanOrEqual(12);
  });

  it("shows a dash and says so when no member has a quote, never a made-up average", async () => {
    renderPanel();
    const card = within(await cardFor("Quiet Theme"));
    await waitFor(() => expect(card.getByText(copy.avgUnavailable)).toBeTruthy());
    expect(card.getByText("—")).toBeTruthy();
    expect(card.queryByRole("img")).toBeNull();
  });

  it("filters by search over the theme name", async () => {
    renderPanel();
    await cardFor("Laser Module");
    fireEvent.change(screen.getByLabelText(copy.searchLabel), { target: { value: "quiet" } });
    expect(screen.queryByRole("heading", { name: "Laser Module" })).toBeNull();
    expect(screen.getByRole("heading", { name: "Quiet Theme" })).toBeTruthy();
    fireEvent.change(screen.getByLabelText(copy.searchLabel), { target: { value: "zzz" } });
    expect(screen.getByText(copy.noMatch)).toBeTruthy();
  });

  it("only lists concept themes", async () => {
    vi.mocked(getThemes).mockResolvedValue({
      theme_count: 2,
      concept_locked: false,
      themes: [LASER, { ...QUIET, kind: "industry" }],
    });
    renderPanel();
    await cardFor("Laser Module");
    expect(screen.queryByRole("heading", { name: "Quiet Theme" })).toBeNull();
  });

  it("shows an empty state when there are no concept themes", async () => {
    vi.mocked(getThemes).mockResolvedValue({ theme_count: 0, themes: [], concept_locked: false });
    renderPanel();
    expect(await screen.findByText(copy.empty)).toBeTruthy();
  });

  it("shows a retryable error when the themes cannot be loaded", async () => {
    vi.mocked(getThemes).mockRejectedValue(new Error("boom"));
    renderPanel();
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});

describe("ThemeHeatPanel Pro preview", () => {
  const lock = messages.featureLock;

  it("keeps the cards and adds one prompt when concept_locked", async () => {
    vi.mocked(getThemes).mockResolvedValue({
      theme_count: 1,
      themes: [LASER],
      concept_locked: true,
    });
    renderPanel();
    await cardFor("Laser Module");
    const row = document.querySelector("[data-feature-lock]");
    expect(row?.textContent).toContain(lock.themeTab.more);
  });

  it("shows no prompt when not locked", async () => {
    renderPanel();
    await cardFor("Laser Module");
    expect(document.querySelector("[data-feature-lock]")).toBeNull();
  });
});
