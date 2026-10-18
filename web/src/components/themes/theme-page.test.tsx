// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemePage } from "@/components/themes/theme-page";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { getThemeMembers, getThemes } from "@/lib/api/theme";
import { messages, renderWithIntl } from "@/test/render";
import type { ThemeMember, ThemeMembersResponse, ThemePosition } from "@/types/theme";

vi.mock("@/lib/api/theme", () => ({ getThemes: vi.fn(), getThemeMembers: vi.fn() }));
vi.mock("@/hooks/use-symbol-quotes", () => ({ useSymbolQuotes: vi.fn() }));
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

const copy = messages.themes.page;

// Invented companies only.
function member(symbol: string, position: ThemePosition | null): ThemeMember {
  return { symbol, market: "TW", sub_industry: "", confidence: 1, source: "seed", position };
}

function response(
  members: ThemeMember[],
  extra: Partial<ThemeMembersResponse> = {},
): ThemeMembersResponse {
  return {
    theme_key: "laser-module",
    found: true,
    name_zh: "Laser Module",
    name_en: "Laser Module EN",
    kind: "concept",
    member_count: members.length,
    description: "Modules that steer light.",
    related_keys: ["film-coating"],
    members,
    message: "",
    ...extra,
  };
}

const CHANGES: Record<string, number | null> = { "1001": 1.25, "1002": -0.5, "1003": null };

function renderPage(): void {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithIntl(
    <QueryClientProvider client={client}>
      <ThemePage themeKey="laser-module" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(getThemes).mockResolvedValue({
    theme_count: 1,
    themes: [
      {
        key: "film-coating",
        name_zh: "Film Coating",
        name_en: "",
        kind: "concept",
        status: "active",
        member_count: 4,
        description: "",
        related_keys: [],
      },
    ],
  });
  vi.mocked(useSymbolQuotes).mockImplementation((codes) => ({
    quotes: codes.map((code) => ({
      code,
      name: `Name ${code}`,
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

describe("ThemePage", () => {
  it("shows the header, the related themes by name, and the member count", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([member("1001", "upstream"), member("1002", "downstream")]),
    );
    renderPage();
    expect(await screen.findByRole("heading", { level: 1 })).toBeTruthy();
    expect(screen.getByText("Modules that steer light.")).toBeTruthy();
    expect(screen.getByText(copy.memberCount.replace("{count}", "2"))).toBeTruthy();
    const related = await screen.findByRole("link", { name: "Film Coating" });
    expect(related.getAttribute("href")).toBe("/themes/film-coating");
  });

  it("lays members out upstream, midstream and downstream with their real daily change", async () => {
    const members = [
      member("1001", "upstream"),
      member("1002", "downstream"),
      member("1003", "downstream"),
    ];
    vi.mocked(getThemeMembers).mockResolvedValue(response(members));
    renderPage();
    const board = await screen.findByRole("region", { name: copy.boardAria });
    const upstream = within(within(board).getByRole("region", { name: copy.position.upstream }));
    const link = upstream.getByRole("link");
    expect(link.getAttribute("href")).toBe("/stock/1001?market=TW");
    expect(upstream.getByText("Name 1001")).toBeTruthy();
    expect(upstream.getByRole("img", { name: /1\.25%/ })).toBeTruthy();
    const downstreamColumn = within(board).getByRole("region", { name: copy.position.downstream });
    const downstream = within(downstreamColumn);
    expect(downstream.getByRole("img", { name: /0\.50%/ })).toBeTruthy();
    // A member with no quote shows a dash, never an invented number.
    expect(downstream.getByText("—")).toBeTruthy();
    // The midstream column is kept but says it is empty.
    const midstream = within(within(board).getByRole("region", { name: copy.position.midstream }));
    expect(midstream.getByText(copy.emptyColumn)).toBeTruthy();
  });

  it("lists unplaced members as related companies and hides the board when none is placed", async () => {
    const members = [member("1001", null), member("1002", "related")];
    vi.mocked(getThemeMembers).mockResolvedValue(response(members));
    renderPage();
    const strip = await screen.findByRole("region", { name: copy.relatedCompanies });
    expect(within(strip).getAllByRole("link")).toHaveLength(2);
    expect(screen.queryByRole("region", { name: copy.boardAria })).toBeNull();
  });

  it("asks for quotes for a bounded sample until the reader shows all", async () => {
    const many = Array.from({ length: 30 }, (_, i) => member(`${2000 + i}`, "upstream"));
    vi.mocked(getThemeMembers).mockResolvedValue(response(many));
    renderPage();
    const board = await screen.findByRole("region", { name: copy.boardAria });
    expect(within(board).getAllByRole("link")).toHaveLength(12);
    const sizes = vi.mocked(useSymbolQuotes).mock.calls.map(([codes]) => codes.length);
    expect(Math.max(...sizes)).toBeLessThanOrEqual(12);
    const toggle = screen.getByRole("button", { name: copy.showAll.replace("{count}", "18") });
    fireEvent.click(toggle);
    expect(within(board).getAllByRole("link")).toHaveLength(30);
    const less = screen.getByRole("button", { name: copy.showFewer });
    expect(less.getAttribute("aria-expanded")).toBe("true");
  });

  it("says so when the theme is not found", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(response([], { found: false, kind: null }));
    renderPage();
    expect(await screen.findByText(copy.notFoundTitle)).toBeTruthy();
    const back = screen.getByRole("link", { name: copy.backToThemes });
    expect(back.getAttribute("href")).toBe("/market");
  });

  it("says so when the theme has no members yet", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(response([]));
    renderPage();
    expect(await screen.findByText(copy.emptyTitle)).toBeTruthy();
  });

  it("shows a retryable error when the theme cannot be loaded", async () => {
    vi.mocked(getThemeMembers).mockRejectedValue(new Error("boom"));
    renderPage();
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });

  it("renders a shaped skeleton, not content, while loading", () => {
    vi.mocked(getThemeMembers).mockReturnValue(new Promise(() => undefined));
    renderPage();
    expect(screen.queryByRole("heading")).toBeNull();
  });
});
