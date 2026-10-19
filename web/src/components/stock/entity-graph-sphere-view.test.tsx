// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { EntityGraph } from "@/components/stock/entity-graph";
import type { EntityGraphSphereProps } from "@/components/stock/entity-graph-sphere";
import { getEntityGraph } from "@/lib/api/entity-graph";
import { getThemes } from "@/lib/api/theme";
import { isWebGLAvailable } from "@/lib/webgl-support";
import { messages, renderWithIntl } from "@/test/render";
import type { EntityGraphResponse, EntityNode } from "@/types/entity-graph";

vi.mock("@/lib/api/entity-graph", () => ({
  ENTITY_GRAPH_LIMIT: 60,
  getEntityGraph: vi.fn(),
}));
vi.mock("@/lib/api/theme", () => ({ getThemes: vi.fn(), getThemeMembers: vi.fn() }));
vi.mock("@/lib/webgl-support", () => ({ isWebGLAvailable: vi.fn() }));
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

// A stand-in for the lazy three.js scene: it exposes what the real one is given, and lets a test
// drive the callbacks the scene would call.
let failOnRender = false;
vi.mock("@/components/stock/entity-graph-sphere-panel", () => ({
  EntityGraphSpherePanel: (props: EntityGraphSphereProps & { onFailed: () => void }) => {
    if (failOnRender) {
      // Mirrors the boundary: the scene throws, the panel reports it and renders nothing.
      queueMicrotask(props.onFailed);
      return null;
    }
    return (
      <div data-testid="sphere" data-selected={props.selectedId ?? ""}>
        <span data-testid="sphere-count">{props.nodes.length}</span>
        {props.nodes
          .filter((n) => !n.focus)
          .map((n) => (
            <button key={n.id} type="button" onClick={() => props.onSelect(n.id)}>
              pick {n.label}
            </button>
          ))}
        <button type="button" onClick={props.onBackToFlat}>
          {messages.stock.industry.entityGraph.backToFlat}
        </button>
      </div>
    );
  },
}));

const copy = messages.stock.industry.entityGraph;

const FOCUS: EntityNode = {
  id: "1001",
  label: "Acme Optics",
  type: "tw_company",
  symbol: "1001",
  market: "TW",
  weight: 12,
  focus: true,
};
const GRAPH: EntityGraphResponse = {
  symbol: "1001",
  market: "TW",
  locked: true,
  hidden_count: 4,
  nodes: [
    FOCUS,
    {
      id: "film",
      label: "Thin Film",
      type: "material",
      symbol: null,
      market: null,
      weight: 2,
      focus: false,
    },
    {
      id: "1002",
      label: "Beta Assembly",
      type: "tw_company",
      symbol: "1002",
      market: "TW",
      weight: 5,
      focus: false,
    },
  ],
  links: [
    { source: "1001", target: "film", weight: 2, kind: "mention" },
    { source: "1001", target: "1002", weight: 2, kind: "supply" },
  ],
};

async function renderGraph(): Promise<void> {
  vi.mocked(getEntityGraph).mockResolvedValue(GRAPH);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithIntl(
    <QueryClientProvider client={client}>
      <EntityGraph symbol="1001" market="TW" />
    </QueryClientProvider>,
  );
  await screen.findByRole("region", { name: copy.listTitle });
}

beforeEach(() => {
  failOnRender = false;
  vi.mocked(getThemes).mockResolvedValue({
    theme_count: 0,
    concept_locked: false,
    themes: [],
  });
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("EntityGraph view toggle", () => {
  it("offers no toggle where WebGL is unavailable, and the flat graph stays", async () => {
    vi.mocked(isWebGLAvailable).mockReturnValue(false);
    await renderGraph();
    expect(screen.queryByRole("tablist", { name: copy.viewLabel })).toBeNull();
    expect(screen.queryByTestId("sphere")).toBeNull();
  });

  it("is a two-tab strip that opens on the flat view", async () => {
    vi.mocked(isWebGLAvailable).mockReturnValue(true);
    await renderGraph();
    const strip = await screen.findByRole("tablist", { name: copy.viewLabel });
    const tabs = within(strip).getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([copy.viewFlat, copy.viewSphere]);
    expect(tabs.map((tab) => tab.getAttribute("aria-selected"))).toEqual(["true", "false"]);
    expect(screen.queryByTestId("sphere")).toBeNull();
    expect(document.querySelector("svg[role='group']")).not.toBeNull();
  });

  it("switches to the sphere with the same nodes, keeping the list and the lock row", async () => {
    vi.mocked(isWebGLAvailable).mockReturnValue(true);
    await renderGraph();
    fireEvent.click(await screen.findByRole("tab", { name: copy.viewSphere }));
    expect(screen.getByTestId("sphere-count").textContent).toBe("3");
    expect(screen.getByRole("tab", { name: copy.viewSphere }).getAttribute("aria-selected")).toBe(
      "true",
    );
    // The flat canvas is replaced; the accessible list and the Pro lock row are still there.
    expect(document.querySelector("svg[role='group']")).toBeNull();
    expect(screen.getByRole("region", { name: copy.listTitle })).toBeTruthy();
    expect(screen.getByText(/還有 4 個節點/)).toBeTruthy();
  });

  it("opens the same detail card when a node is picked in the sphere", async () => {
    vi.mocked(isWebGLAvailable).mockReturnValue(true);
    await renderGraph();
    fireEvent.click(await screen.findByRole("tab", { name: copy.viewSphere }));
    fireEvent.click(screen.getByRole("button", { name: "pick Thin Film" }));
    const card = screen.getByRole("region", {
      name: copy.detailTitle.replace("{name}", "Thin Film"),
    });
    expect(within(card).getByText(copy.detailNoTheme)).toBeTruthy();
    expect(screen.getByTestId("sphere").getAttribute("data-selected")).toBe("film");

    // A company leads to its stock page from the card.
    fireEvent.click(screen.getByRole("button", { name: "pick Beta Assembly" }));
    const stockCard = screen.getByRole("region", {
      name: copy.detailTitle.replace("{name}", "Beta Assembly"),
    });
    const open = within(stockCard).getByRole("link", { name: copy.detailOpenStock });
    expect(open.getAttribute("href")).toBe("/stock/1002?market=TW");
  });

  it("returns to the flat view from the sphere's own button", async () => {
    vi.mocked(isWebGLAvailable).mockReturnValue(true);
    await renderGraph();
    fireEvent.click(await screen.findByRole("tab", { name: copy.viewSphere }));
    fireEvent.click(screen.getByRole("button", { name: copy.backToFlat }));
    expect(screen.queryByTestId("sphere")).toBeNull();
    expect(screen.getByRole("tab", { name: copy.viewFlat }).getAttribute("aria-selected")).toBe(
      "true",
    );
  });

  it("falls back to the flat view with a calm note when the scene cannot start", async () => {
    vi.mocked(isWebGLAvailable).mockReturnValue(true);
    failOnRender = true;
    await renderGraph();
    fireEvent.click(await screen.findByRole("tab", { name: copy.viewSphere }));
    await waitFor(() => expect(screen.getByText(copy.sphereFailed)).toBeTruthy());
    expect(screen.queryByRole("tablist", { name: copy.viewLabel })).toBeNull();
    expect(document.querySelector("svg[role='group']")).not.toBeNull();
  });
});
