// @vitest-environment jsdom
import { cleanup, fireEvent } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ThemeCards } from "@/components/screener/theme-cards";
import { useThemeMembers, useThemes } from "@/hooks/use-themes";
import { renderWithIntl } from "@/test/render";

vi.mock("@/hooks/use-themes", () => ({ useThemes: vi.fn(), useThemeMembers: vi.fn() }));
vi.mock("@/components/screener/screener-loading", () => ({
  ScreenerLoading: (): ReactNode => <div data-testid="loading" />,
}));
vi.mock("@/components/screener/screener-table", () => ({
  ScreenerTable: ({
    survivors,
  }: {
    survivors: { symbol: string; matched_conditions: string[] }[];
  }): ReactNode => (
    <ul data-testid="table">
      {survivors.map((s) => (
        <li key={s.symbol}>{`${s.symbol}|${s.matched_conditions.join(",")}`}</li>
      ))}
    </ul>
  ),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({
    href,
    children,
    className,
  }: {
    href: string;
    children: ReactNode;
    className: string;
  }): ReactNode => (
    <a href={href} className={className}>
      {children}
    </a>
  ),
}));

function theme(key: string, name_zh: string, name_en: string, kind: "industry" | "concept") {
  return {
    key,
    name_zh,
    name_en,
    kind,
    status: "ok",
    member_count: 3,
    description: "",
    related_keys: [],
  };
}

const THEMES = [
  theme("ai", "人工智慧", "AI Server", "concept"),
  theme("semi", "半導體", "Semiconductor", "industry"),
];

function member(symbol: string, sub_industry: string) {
  return { symbol, market: "TW", sub_industry, confidence: 1, source: "x", position: null };
}

function membersResponse(kind: "industry" | "concept", found = true) {
  return {
    theme_key: "ai",
    found,
    name_zh: "",
    name_en: "",
    kind,
    member_count: 3,
    description: "",
    related_keys: [],
    members: [
      member("2330", "晶圓"),
      member("2454", "IC設計"),
      member("3034", "IC設計"),
      member("1101", ""),
    ],
    message: "",
    locked: false,
    hidden_count: 0,
  };
}

function setThemes(state: Record<string, unknown>): void {
  vi.mocked(useThemes).mockReturnValue(state as never);
}
function setMembers(state: Record<string, unknown>): void {
  vi.mocked(useThemeMembers).mockReturnValue(state as never);
}

beforeEach(() => {
  setThemes({ isLoading: false, isError: false, data: { theme_count: 2, themes: THEMES } });
  setMembers({ isLoading: false, data: undefined });
});
afterEach(cleanup);

function chips(c: HTMLElement): HTMLElement[] {
  return Array.from(c.querySelectorAll<HTMLElement>("button[aria-pressed]")).filter(
    (b) => !b.className.includes("min-h-8"),
  );
}
function subChips(c: HTMLElement): HTMLElement[] {
  return Array.from(c.querySelectorAll<HTMLElement>("button[aria-pressed]")).filter((b) =>
    b.className.includes("min-h-8"),
  );
}
function texts(list: HTMLElement[]): (string | null)[] {
  return list.map((b) => b.textContent);
}

describe("ThemeCards", () => {
  it("shows the loading skeleton while themes load", () => {
    setThemes({ isLoading: true, isError: false, data: undefined });
    const { container } = renderWithIntl(<ThemeCards />);
    expect(container.querySelectorAll("[data-testid=loading]")).toHaveLength(1);
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  it("shows the error line on error or missing data", () => {
    setThemes({ isLoading: false, isError: true, data: undefined });
    const a = renderWithIntl(<ThemeCards />).container.innerHTML;
    cleanup();
    setThemes({ isLoading: false, isError: false, data: undefined });
    const b = renderWithIntl(<ThemeCards />).container.innerHTML;
    expect(a).toBe(b);
    expect(a).toMatchSnapshot();
  });

  it("renders the kind tabs and every theme chip", () => {
    const { container } = renderWithIntl(<ThemeCards />);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("filters by kind", () => {
    const { container } = renderWithIntl(<ThemeCards />);
    const tabs = container.querySelectorAll<HTMLElement>("[role=tab]");
    fireEvent.click(tabs[2] as HTMLElement);
    expect(tabs[2]?.getAttribute("aria-selected")).toBe("true");
    expect(texts(chips(container))).toEqual(["人工智慧3"]);
    fireEvent.click(tabs[1] as HTMLElement);
    expect(texts(chips(container))).toEqual(["半導體3"]);
    fireEvent.click(tabs[0] as HTMLElement);
    expect(chips(container)).toHaveLength(2);
  });

  it("filters by search over zh, case-insensitive en, and ignores blank search", () => {
    const { container } = renderWithIntl(<ThemeCards />);
    const input = container.querySelector("input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "半導" } });
    expect(texts(chips(container))).toEqual(["半導體3"]);
    fireEvent.change(input, { target: { value: "ai SERVER" } });
    expect(texts(chips(container))).toEqual(["人工智慧3"]);
    fireEvent.change(input, { target: { value: "   " } });
    expect(chips(container)).toHaveLength(2);
    fireEvent.change(input, { target: { value: "zzz" } });
    expect(chips(container)).toHaveLength(0);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("selects and deselects a theme, passing the key to useThemeMembers", () => {
    const { container } = renderWithIntl(<ThemeCards />);
    expect(vi.mocked(useThemeMembers).mock.lastCall?.[0]).toBeNull();
    fireEvent.click(chips(container)[1] as HTMLElement);
    expect(vi.mocked(useThemeMembers).mock.lastCall?.[0]).toBe("semi");
    expect(chips(container)[1]?.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(chips(container)[1] as HTMLElement);
    expect(vi.mocked(useThemeMembers).mock.lastCall?.[0]).toBeNull();
  });

  it("shows loading while members load, only once a theme is active", () => {
    setMembers({ isLoading: true, data: undefined });
    const { container } = renderWithIntl(<ThemeCards />);
    expect(container.querySelectorAll("[data-testid=loading]")).toHaveLength(0);
    fireEvent.click(chips(container)[0] as HTMLElement);
    expect(container.querySelectorAll("[data-testid=loading]")).toHaveLength(1);
  });

  it("renders members of a concept theme with link and sub-industry filter", () => {
    setMembers({ isLoading: false, data: membersResponse("concept") });
    const { container } = renderWithIntl(<ThemeCards />);
    expect(container.querySelectorAll("[data-testid=table]")).toHaveLength(0);
    fireEvent.click(chips(container)[0] as HTMLElement);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/themes/ai");
    const table = (): (string | null)[] =>
      Array.from(container.querySelectorAll("[data-testid=table] li")).map((li) => li.textContent);
    expect(table()).toEqual(["2330|晶圓", "2454|IC設計", "3034|IC設計", "1101|"]);
    expect(container.innerHTML).toMatchSnapshot();
    expect(subChips(container)).toHaveLength(3);
    fireEvent.click(subChips(container)[2] as HTMLElement);
    expect(table()).toEqual(["2454|IC設計", "3034|IC設計"]);
    expect(subChips(container)[2]?.getAttribute("aria-pressed")).toBe("true");
    expect(subChips(container)[0]?.getAttribute("aria-pressed")).toBe("false");
    expect(container.innerHTML).toMatchSnapshot();
    fireEvent.click(subChips(container)[0] as HTMLElement);
    expect(table()).toHaveLength(4);
    fireEvent.click(subChips(container)[1] as HTMLElement);
    expect(table()).toEqual(["2330|晶圓"]);
    // switching theme resets the sub-industry filter
    fireEvent.click(chips(container)[1] as HTMLElement);
    expect(subChips(container)[0]?.getAttribute("aria-pressed")).toBe("true");
  });

  it("omits the theme-page link for an industry theme", () => {
    setMembers({ isLoading: false, data: membersResponse("industry") });
    const { container } = renderWithIntl(<ThemeCards />);
    fireEvent.click(chips(container)[1] as HTMLElement);
    expect(container.querySelectorAll("a")).toHaveLength(0);
    expect(container.querySelectorAll("[data-testid=table]")).toHaveLength(1);
  });

  it("omits the sub-industry row when no member has one", () => {
    const data = membersResponse("industry");
    data.members = [member("1101", "")];
    setMembers({ isLoading: false, data });
    const { container } = renderWithIntl(<ThemeCards />);
    fireEvent.click(chips(container)[1] as HTMLElement);
    expect(subChips(container)).toHaveLength(0);
  });

  it("shows the error line when the theme is not found", () => {
    setMembers({ isLoading: false, data: membersResponse("industry", false) });
    const { container } = renderWithIntl(<ThemeCards />);
    expect(container.querySelectorAll("p")).toHaveLength(0);
    fireEvent.click(chips(container)[1] as HTMLElement);
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.querySelectorAll("[data-testid=table]")).toHaveLength(0);
  });
});
