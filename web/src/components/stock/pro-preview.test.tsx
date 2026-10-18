// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, screen, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChainPositionBar } from "@/components/stock/chain-position";
import { ConceptThemeChips } from "@/components/stock/concept-theme-chips";
import { EntityGraph } from "@/components/stock/entity-graph";
import { SupplyChainGraph } from "@/components/stock/supply-chain-graph";
import { useIsAnonymous } from "@/hooks/use-is-anonymous";
import { getEntityGraph } from "@/lib/api/entity-graph";
import { getChainPositions, getSupplyChainEdges } from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";
import type { EntityGraphResponse } from "@/types/entity-graph";
import type { Market } from "@/types/settings";
import type { ChainPosition, ChainPositionsResponse } from "@/types/stock/chain-position";
import type { SupplyChainEdge, SupplyChainEdgesResponse } from "@/types/stock/profile";

vi.mock("@/lib/api/stock-detail", () => ({
  getSupplyChainEdges: vi.fn(),
  getChainPositions: vi.fn(),
}));
vi.mock("@/lib/api/entity-graph", () => ({ ENTITY_GRAPH_LIMIT: 60, getEntityGraph: vi.fn() }));
vi.mock("@/lib/api/theme", () => ({
  getThemes: vi.fn().mockResolvedValue({ theme_count: 0, themes: [], concept_locked: false }),
  getThemeMembers: vi.fn(),
}));
vi.mock("@/hooks/use-is-anonymous", () => ({ useIsAnonymous: vi.fn() }));
vi.mock("@/hooks/use-symbol-quotes", () => ({
  useSymbolQuotes: vi.fn((codes: string[], _market: Market) => ({
    quotes: codes.map((code) => ({
      code,
      name: null,
      industryCategory: null,
      price: 10,
      changePct: null,
      sparkline: [],
      volume: null,
    })),
    isLoading: false,
    isError: false,
  })),
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

const lock = messages.featureLock;

function withClient(ui: ReactNode): ReactNode {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>;
}

beforeEach(() => {
  vi.mocked(useIsAnonymous).mockReturnValue(false);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

// Invented companies only.
const edge: SupplyChainEdge = {
  from_symbol: "1001",
  to_symbol: "1002",
  market: "TW",
  from_market: "TW",
  to_market: "TW",
  edge_type: "supplies",
  confidence: "medium",
  revenue_pct: null,
  source: "research",
  from_name: "Acme Parts",
  to_name: "Beta Assembly",
  sources: [],
};

function chain(extra: Partial<SupplyChainEdgesResponse> = {}): SupplyChainEdgesResponse {
  return {
    symbol: "1001",
    market: "TW",
    edge_count: 1,
    edges: [edge],
    locked: false,
    hidden_count: 0,
    sources_locked: false,
    ...extra,
  };
}

describe("supply chain preview", () => {
  it("shows the 'more companies' row with the server's count and a source lock when locked", async () => {
    vi.mocked(getSupplyChainEdges).mockResolvedValue(
      chain({ locked: true, hidden_count: 4, sources_locked: true }),
    );
    renderWithIntl(withClient(<SupplyChainGraph symbol="1001" market="TW" />));
    await screen.findByRole("link", { name: /Beta Assembly 1002/ });

    const row = document.querySelector("[data-feature-lock]:not(a):not(button)");
    expect(row?.textContent).toBe(
      `${lock.supplyChain.more.replace("{count}", "4")} · ${lock.supplyChain.benefit}${lock.upgrade}`,
    );
    const sourceLock = screen.getByRole("link", { name: lock.supplyChain.sourcesLocked });
    expect(sourceLock.getAttribute("href")).toBe("/settings/billing");
  });

  it("shows no prompt for a Pro answer (locked false)", async () => {
    vi.mocked(getSupplyChainEdges).mockResolvedValue(chain());
    renderWithIntl(withClient(<SupplyChainGraph symbol="1001" market="TW" />));
    await screen.findByRole("link", { name: /Beta Assembly 1002/ });
    expect(document.querySelector("[data-feature-lock]")).toBeNull();
  });

  it("keeps the real sources button when sources are present", async () => {
    vi.mocked(getSupplyChainEdges).mockResolvedValue(
      chain({
        edges: [
          { ...edge, sources: [{ publisher: "Example Wire", url: "https://example.test/a" }] },
        ],
      }),
    );
    renderWithIntl(withClient(<SupplyChainGraph symbol="1001" market="TW" />));
    await screen.findByRole("link", { name: /Beta Assembly 1002/ });
    expect(screen.queryByRole("link", { name: lock.supplyChain.sourcesLocked })).toBeNull();
  });
});

const position: ChainPosition = {
  chain_key: "chain_alpha",
  chain_name: "Alpha Chain",
  position: "upstream",
  is_primary: true,
  method: "evidence",
  confidence: 0.9,
  reasons: [],
  computed_at: null,
};

function positions(extra: Partial<ChainPositionsResponse>): ChainPositionsResponse {
  return {
    symbol: "1001",
    market: "TW",
    positions: [position],
    locked: false,
    hidden_count: 0,
    ...extra,
  };
}

describe("chain position preview", () => {
  it("swaps the 為什麼 popover and the other chains for Pro chips when locked", async () => {
    vi.mocked(getChainPositions).mockResolvedValue(positions({ locked: true, hidden_count: 2 }));
    renderWithIntl(withClient(<ChainPositionBar symbol="1001" market="TW" />));
    await screen.findByRole("img", { name: /Alpha Chain/ });

    const more = screen.getByRole("link", {
      name: lock.chainPosition.moreChains.replace("{count}", "2"),
    });
    expect(more.getAttribute("href")).toBe("/settings/billing");
    expect(screen.getByRole("link", { name: lock.chainPosition.why })).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("shows the normal popover and no chip when not locked", async () => {
    vi.mocked(getChainPositions).mockResolvedValue(
      positions({ positions: [{ ...position, reasons: [{ code: "graph_layering" }] }] }),
    );
    renderWithIntl(withClient(<ChainPositionBar symbol="1001" market="TW" />));
    await screen.findByRole("img", { name: /Alpha Chain/ });
    expect(screen.getByRole("button")).toBeTruthy();
    expect(document.querySelector("[data-feature-lock]")).toBeNull();
  });

  it("shows no 'more chains' chip when locked with nothing withheld", async () => {
    vi.mocked(getChainPositions).mockResolvedValue(positions({ locked: true, hidden_count: 0 }));
    renderWithIntl(withClient(<ChainPositionBar symbol="1001" market="TW" />));
    await screen.findByRole("img", { name: /Alpha Chain/ });
    expect(screen.getByRole("link", { name: lock.chainPosition.why })).toBeTruthy();
    expect(
      screen.queryByRole("link", {
        name: lock.chainPosition.moreChains.replace("{count}", "0"),
      }),
    ).toBeNull();
  });
});

function graph(extra: Partial<EntityGraphResponse>): EntityGraphResponse {
  return {
    symbol: "1001",
    market: "TW",
    nodes: [
      {
        id: "1001",
        label: "Acme Optics",
        type: "tw_company",
        symbol: "1001",
        market: "TW",
        weight: 9,
        focus: true,
      },
      {
        id: "laser",
        label: "Laser Module",
        type: "technology",
        symbol: null,
        market: null,
        weight: 5,
        focus: false,
      },
    ],
    links: [{ source: "1001", target: "laser", weight: 5, kind: "mention" }],
    locked: false,
    hidden_count: 0,
    ...extra,
  };
}

describe("entity graph preview", () => {
  it("adds the note with the hidden node count when locked", async () => {
    vi.mocked(getEntityGraph).mockResolvedValue(graph({ locked: true, hidden_count: 7 }));
    renderWithIntl(withClient(<EntityGraph symbol="1001" market="TW" />));
    await screen.findByRole("region", { name: messages.stock.industry.entityGraph.listTitle });

    const row = document.querySelector("[data-feature-lock]");
    expect(within(row as HTMLElement).getByText(/7/)).toBeTruthy();
    expect(row?.textContent).toContain(lock.entityGraph.benefit);
  });

  it("adds nothing for an unlocked graph", async () => {
    vi.mocked(getEntityGraph).mockResolvedValue(graph({}));
    renderWithIntl(withClient(<EntityGraph symbol="1001" market="TW" />));
    await screen.findByRole("region", { name: messages.stock.industry.entityGraph.listTitle });
    expect(document.querySelector("[data-feature-lock]")).toBeNull();
  });

  it("sends an anonymous caller to sign-in instead of billing", async () => {
    vi.mocked(useIsAnonymous).mockReturnValue(true);
    vi.mocked(getEntityGraph).mockResolvedValue(graph({ locked: true, hidden_count: 7 }));
    renderWithIntl(withClient(<EntityGraph symbol="1001" market="TW" />));
    await screen.findByRole("region", { name: messages.stock.industry.entityGraph.listTitle });
    expect(screen.getByRole("button", { name: lock.signIn })).toBeTruthy();
    expect(screen.queryByRole("link", { name: lock.upgrade })).toBeNull();
  });
});

describe("concept theme chips preview", () => {
  const themes = [
    {
      theme_key: "laser-module",
      name_zh: "Laser Module",
      kind: "concept",
      sub_industry: "",
      confidence: 1,
    },
  ];

  it("keeps the chips and adds one lock chip when concept_locked", () => {
    renderWithIntl(<ConceptThemeChips themes={themes} locked />);
    expect(screen.getByRole("link", { name: /Laser Module/ })).toBeTruthy();
    expect(screen.getByRole("link", { name: lock.conceptChips.more })).toBeTruthy();
  });

  it("shows no lock chip for an unlocked answer", () => {
    renderWithIntl(<ConceptThemeChips themes={themes} />);
    expect(screen.queryByRole("link", { name: lock.conceptChips.more })).toBeNull();
  });

  it("shows just the lock chip when every concept theme was withheld", () => {
    renderWithIntl(<ConceptThemeChips themes={[]} locked />);
    expect(screen.getByRole("link", { name: lock.conceptChips.more })).toBeTruthy();
  });
});
