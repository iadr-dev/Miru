// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EntityGraph } from "@/components/stock/entity-graph";
import { getEntityGraph } from "@/lib/api/entity-graph";
import { getThemes } from "@/lib/api/theme";
import { messages, renderWithIntl } from "@/test/render";
import type { EntityGraphResponse, EntityNode, EntityType } from "@/types/entity-graph";

vi.mock("@/lib/api/entity-graph", () => ({
  ENTITY_GRAPH_LIMIT: 60,
  getEntityGraph: vi.fn(),
}));
vi.mock("@/lib/api/theme", () => ({ getThemes: vi.fn(), getThemeMembers: vi.fn() }));
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

const copy = messages.stock.industry.entityGraph;

// Invented companies and entities only.
function entity(id: string, label: string, type: EntityType, weight: number): EntityNode {
  return { id, label, type, symbol: null, market: null, weight, focus: false };
}

function company(
  id: string,
  label: string,
  weight: number,
  options: { market?: "TW" | "US"; focus?: boolean } = {},
): EntityNode {
  const market = options.market ?? "TW";
  return {
    id,
    label,
    type: market === "TW" ? "tw_company" : "intl_company",
    symbol: id,
    market,
    weight,
    focus: options.focus ?? false,
  };
}

const GRAPH: EntityGraphResponse = {
  symbol: "1001",
  market: "TW",
  locked: false,
  hidden_count: 0,
  nodes: [
    company("1001", "Acme Optics", 12, { focus: true }),
    entity("laser", "Laser Module", "technology", 8),
    entity("film", "Thin Film", "material", 2),
    company("1002", "Beta Assembly", 5),
    company("ZED", "Zed Corp", 1, { market: "US" }),
  ],
  links: [
    { source: "1001", target: "laser", weight: 8, kind: "mention" },
    { source: "1001", target: "film", weight: 2, kind: "mention" },
    { source: "1002", target: "laser", weight: 3, kind: "cooccurrence" },
    { source: "1001", target: "1002", weight: 2, kind: "supply" },
  ],
};

function renderGraph(graph: EntityGraphResponse = GRAPH): { container: HTMLElement } {
  vi.mocked(getEntityGraph).mockResolvedValue(graph);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = renderWithIntl(
    <QueryClientProvider client={client}>
      <EntityGraph symbol="1001" market="TW" />
    </QueryClientProvider>,
  );
  return { container };
}

async function ready(): Promise<void> {
  await screen.findByRole("region", { name: copy.listTitle });
}

function listRegion(): HTMLElement {
  return screen.getByRole("region", { name: copy.listTitle });
}

function canvasNode(container: HTMLElement, label: string): Element | null {
  return container.querySelector(`svg [aria-label^="${label},"]`);
}

/** The opacity a node is drawn at: 1 normally, 0.22 when something else is highlighted. */
function opacityOf(container: HTMLElement, label: string): string | null | undefined {
  return canvasNode(container, label)?.firstElementChild?.getAttribute("opacity");
}

beforeEach(() => {
  vi.mocked(getThemes).mockResolvedValue({
    theme_count: 1,
    concept_locked: false,
    themes: [
      {
        key: "laser-module",
        name_zh: "Laser Module",
        name_en: "",
        kind: "concept",
        status: "active",
        member_count: 3,
        description: "",
        related_keys: [],
      },
    ],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EntityGraph", () => {
  it("lists the entities grouped by type with a count per group", async () => {
    renderGraph();
    await ready();
    const list = within(listRegion());
    for (const type of [copy.type.technology, copy.type.tw_company]) {
      const heading = list.getByRole("heading", { name: new RegExp(`^${type}`) });
      expect(heading.textContent).toContain("(1)");
    }
    // The company in focus is the subject of the page, not a row in its own list.
    expect(list.queryByText("Acme Optics")).toBeNull();
  });

  it("links a company to its stock page and a technology to its concept theme", async () => {
    const { container } = renderGraph();
    await ready();
    expect(container.querySelector('svg a[href="/stock/1002?market=TW"]')).not.toBeNull();
    expect(container.querySelector('svg a[href="/stock/ZED?market=US"]')).not.toBeNull();
    expect(container.querySelector('svg a[href="/themes/laser-module"]')).not.toBeNull();
    // The same link is also in the list, so a phone (which hides the canvas) can reach it.
    expect(container.querySelectorAll('a[href="/themes/laser-module"]')).toHaveLength(2);
  });

  it("shows a detail card for an entity that has no theme page", async () => {
    renderGraph();
    await ready();
    fireEvent.click(within(listRegion()).getByRole("button", { name: /Thin Film/ }));
    const cardName = copy.detailTitle.replace("{name}", "Thin Film");
    const card = screen.getByRole("region", { name: cardName });
    expect(within(card).getByText(copy.detailNoTheme)).toBeTruthy();
    expect(within(card).getByText(/Acme Optics/)).toBeTruthy();
    fireEvent.click(within(card).getByRole("button", { name: copy.detailClose }));
    expect(screen.queryByRole("region", { name: cardName })).toBeNull();
  });

  it("hides nodes lighter than the minimum-weight slider, and the company in focus stays", async () => {
    const { container } = renderGraph();
    await ready();
    const slider = screen.getByLabelText(copy.sliderLabel);
    expect(slider.getAttribute("max")).toBe("8");
    fireEvent.change(slider, { target: { value: "5" } });
    const list = within(listRegion());
    expect(list.queryByText("Thin Film")).toBeNull();
    expect(list.queryByText("Zed Corp")).toBeNull();
    expect(list.getByText("Beta Assembly")).toBeTruthy();
    expect(canvasNode(container, "Acme Optics")).not.toBeNull();
    expect(canvasNode(container, "Thin Film")).toBeNull();
  });

  it("toggles a type off and on with its chip", async () => {
    renderGraph();
    await ready();
    const chip = screen.getByRole("button", { name: new RegExp(`^${copy.type.material}`) });
    expect(chip.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(chip);
    expect(chip.getAttribute("aria-pressed")).toBe("false");
    expect(within(listRegion()).queryByText("Thin Film")).toBeNull();
    fireEvent.click(chip);
    expect(within(listRegion()).getByText("Thin Film")).toBeTruthy();
  });

  it("search counts matches and dims the nodes that do not match", async () => {
    const { container } = renderGraph();
    await ready();
    fireEvent.change(screen.getByLabelText(copy.searchLabel), { target: { value: "laser" } });
    expect(screen.getByText(copy.matches.replace("{count}", "1"))).toBeTruthy();
    expect(opacityOf(container, "Thin Film")).toBe("0.22");
    expect(opacityOf(container, "Laser Module")).toBe("1");
    fireEvent.change(screen.getByLabelText(copy.searchLabel), { target: { value: "zzz" } });
    expect(screen.getByText(copy.noMatches)).toBeTruthy();
  });

  it("highlights a node's neighbours on hover and on keyboard focus, and dims the rest", async () => {
    const { container } = renderGraph();
    await ready();
    const laser = container.querySelector('svg a[href="/themes/laser-module"]');
    if (!laser) throw new Error("laser node missing");
    fireEvent.mouseEnter(laser);
    expect(opacityOf(container, "Beta Assembly")).toBe("1");
    expect(opacityOf(container, "Thin Film")).toBe("0.22");
    fireEvent.mouseLeave(laser);
    expect(opacityOf(container, "Thin Film")).toBe("1");
    fireEvent.focus(laser);
    expect(opacityOf(container, "Thin Film")).toBe("0.22");
    fireEvent.blur(laser);
    expect(opacityOf(container, "Thin Film")).toBe("1");
  });

  it("draws supply links with an arrow and never calls a co-mention a relationship", async () => {
    const { container } = renderGraph();
    await ready();
    const canvas = 'svg[role="group"]';
    expect(container.querySelectorAll(`${canvas} line[marker-end]`)).toHaveLength(1);
    expect(container.querySelectorAll(`${canvas} line[stroke-dasharray]`)).toHaveLength(1);
    expect(screen.getAllByText(copy.linkCooccurrence).length).toBeGreaterThan(0);
    expect(screen.getByText(copy.cooccurrenceNote)).toBeTruthy();
  });

  it("says plainly that there are no mentions when the company has none", async () => {
    renderGraph({ ...GRAPH, nodes: GRAPH.nodes.filter((n) => n.focus), links: [] });
    expect(await screen.findByText(copy.emptyTitle)).toBeTruthy();
    expect(screen.getByText(copy.emptyBody)).toBeTruthy();
    expect(screen.queryByRole("region", { name: copy.listTitle })).toBeNull();
  });

  it("shows a retryable error when the graph cannot be loaded", async () => {
    vi.mocked(getEntityGraph).mockRejectedValue(new Error("boom"));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <EntityGraph symbol="1001" market="TW" />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
  });
});
