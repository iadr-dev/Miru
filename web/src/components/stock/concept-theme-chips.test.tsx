// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ConceptThemeChips } from "@/components/stock/concept-theme-chips";
import { renderWithIntl } from "@/test/render";
import type { SymbolTheme } from "@/types/stock/profile";

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

afterEach(cleanup);

// Invented themes only.
function theme(key: string, name: string, kind: string): SymbolTheme {
  return { theme_key: key, name_zh: name, kind, sub_industry: "", confidence: 1 };
}

describe("ConceptThemeChips", () => {
  it("links each concept theme to its page and leaves industry-chain themes out", () => {
    renderWithIntl(
      <ConceptThemeChips
        themes={[
          theme("laser-module", "Laser Module", "concept"),
          theme("optics-chain", "Optics Chain", "industry"),
          theme("film-coating", "Film Coating", "concept"),
        ]}
      />,
    );
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/themes/laser-module",
      "/themes/film-coating",
    ]);
    expect(screen.queryByText("Optics Chain")).toBeNull();
    expect(screen.getByText("所屬題材")).toBeTruthy();
  });

  it("renders nothing at all when the stock is in no concept theme", () => {
    const { container } = renderWithIntl(
      <ConceptThemeChips themes={[theme("optics-chain", "Optics Chain", "industry")]} />,
    );
    expect(container.firstChild).toBeNull();
    const empty = renderWithIntl(<ConceptThemeChips themes={[]} />);
    expect(empty.container.firstChild).toBeNull();
  });
});
