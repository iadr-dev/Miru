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
    locked: false,
    hidden_count: 0,
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
    concept_locked: false,
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

describe("ThemePage Pro preview", () => {
  const lock = messages.featureLock;

  it("shows the hidden-members row with the server's count when locked", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([member("1001", "upstream")], { locked: true, hidden_count: 9 }),
    );
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    const row = document.querySelector("[data-feature-lock]");
    expect(row?.textContent).toBe(
      `${lock.themePage.more.replace("{count}", "9")} · ${lock.themePage.benefit}${lock.upgrade}`,
    );
    expect(screen.getByRole("link", { name: lock.upgrade }).getAttribute("href")).toBe(
      "/settings/billing",
    );
  });

  it("shows no row when locked but nothing is hidden, or when not locked", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([member("1001", "upstream")], { locked: true, hidden_count: 0 }),
    );
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(document.querySelector("[data-feature-lock]")).toBeNull();
    cleanup();
    vi.mocked(getThemeMembers).mockResolvedValue(response([member("1001", "upstream")]));
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(document.querySelector("[data-feature-lock]")).toBeNull();
  });
});

describe("ThemePage branches", () => {
  const themeOf = (key: string, over: Record<string, unknown> = {}) => ({
    key,
    name_zh: `名稱 ${key}`,
    name_en: "",
    kind: "concept" as const,
    status: "active",
    member_count: 1,
    description: "",
    related_keys: [] as string[],
    ...over,
  });

  it("renders a full page snapshot with a chain, a related strip and every header part", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([
        member("1001", "upstream"),
        member("1002", "midstream"),
        member("1003", "downstream"),
        member("1004", null),
      ]),
    );
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(document.body.innerHTML).toMatchSnapshot();
  });

  it("omits the English name when there is none", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([member("1001", "upstream")], { name_en: "" }),
    );
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(screen.queryByText("Laser Module EN")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 }).textContent).toBe("Laser Module");
  });

  it("falls back to the theme list for the description and related themes", async () => {
    vi.mocked(getThemes).mockResolvedValue({
      theme_count: 2,
      concept_locked: false,
      themes: [
        themeOf("laser-module", { description: "From the list.", related_keys: ["film-coating"] }),
        themeOf("film-coating"),
      ],
    });
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([member("1001", "upstream")], { description: "", related_keys: [] }),
    );
    renderPage();
    expect(await screen.findByText("From the list.")).toBeTruthy();
    const link = await screen.findByRole("link", { name: "名稱 film-coating" });
    expect(link.getAttribute("href")).toBe("/themes/film-coating");
  });

  it("shows neither description nor related themes when both sources are empty", async () => {
    vi.mocked(getThemes).mockResolvedValue({ theme_count: 0, concept_locked: false, themes: [] });
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([member("1001", "upstream")], { description: "", related_keys: [] }),
    );
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(screen.queryByText(copy.relatedTitle)).toBeNull();
    expect(document.querySelector("header p.max-w-prose")).toBeNull();
  });

  it("names a related theme by its key when the list does not know it", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([member("1001", "upstream")], { related_keys: ["unknown-theme"] }),
    );
    renderPage();
    const link = await screen.findByRole("link", { name: "unknown-theme" });
    expect(link.getAttribute("href")).toBe("/themes/unknown-theme");
  });

  it("keeps the page up while the theme list is still loading", async () => {
    vi.mocked(getThemes).mockReturnValue(new Promise(() => undefined));
    vi.mocked(getThemeMembers).mockResolvedValue(response([member("1001", "upstream")]));
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(screen.getByText("Modules that steer light.")).toBeTruthy();
  });

  it("asks US and other-market codes from the right quote source", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(
      response([
        { ...member("AAPL", "upstream"), market: "US" },
        { ...member("1001", "upstream"), market: "JP" },
        member("1002", "upstream"),
        member("1002", "midstream"),
      ]),
    );
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    const last = (market: string): string[] | undefined =>
      vi
        .mocked(useSymbolQuotes)
        .mock.calls.filter(([, m]) => m === market)
        .at(-1)?.[0];
    expect(last("US")).toEqual(["AAPL"]);
    // Duplicates collapse; an unknown market counts as TW.
    expect(last("TW")).toEqual(["1001", "1002"]);
    expect(screen.getAllByText("Name AAPL")).toHaveLength(1);
  });

  it("shows skeletons instead of changes while quotes load", async () => {
    vi.mocked(useSymbolQuotes).mockReturnValue({ quotes: [], isLoading: true, isError: false });
    vi.mocked(getThemeMembers).mockResolvedValue(response([member("1001", "upstream")]));
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText("—")).toBeNull();
  });

  it("collapses back and toggles the related strip too", async () => {
    const many = Array.from({ length: 14 }, (_, i) => member(`${3000 + i}`, null));
    vi.mocked(getThemeMembers).mockResolvedValue(response(many));
    renderPage();
    const strip = await screen.findByRole("region", { name: copy.relatedCompanies });
    expect(within(strip).getAllByRole("link")).toHaveLength(12);
    fireEvent.click(screen.getByRole("button", { name: copy.showAll.replace("{count}", "2") }));
    expect(within(strip).getAllByRole("link")).toHaveLength(14);
    fireEvent.click(screen.getByRole("button", { name: copy.showFewer }));
    expect(within(strip).getAllByRole("link")).toHaveLength(12);
  });

  it("offers no toggle when everything fits", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(response([member("1001", "upstream")]));
    renderPage();
    await screen.findByRole("region", { name: copy.boardAria });
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows the empty card and no board, strip or toggle for a theme with no members", async () => {
    vi.mocked(getThemeMembers).mockResolvedValue(response([]));
    renderPage();
    await screen.findByText(copy.emptyTitle);
    expect(screen.queryByRole("region", { name: copy.boardAria })).toBeNull();
    expect(screen.queryByRole("region", { name: copy.relatedCompanies })).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
