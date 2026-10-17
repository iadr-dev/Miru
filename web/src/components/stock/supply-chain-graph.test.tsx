// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SupplyChainGraph } from "@/components/stock/supply-chain-graph";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import { getSupplyChainEdges } from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";
import type { Market } from "@/types/settings";
import type { SupplyChainEdge, SupplyChainEdgesResponse } from "@/types/stock/profile";

vi.mock("@/lib/api/stock-detail", () => ({ getSupplyChainEdges: vi.fn() }));
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

const grade = messages.stock.industry.supplyChainGrade;

// Invented companies only.
const base: SupplyChainEdge = {
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

function respond(edges: SupplyChainEdge[]): SupplyChainEdgesResponse {
  return { symbol: "1001", market: "TW", edge_count: edges.length, edges };
}

function renderGraph(edges: SupplyChainEdge[]): void {
  vi.mocked(getSupplyChainEdges).mockResolvedValue(respond(edges));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithIntl(
    <QueryClientProvider client={client}>
      <SupplyChainGraph symbol="1001" market="TW" />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.mocked(useSymbolQuotes).mockImplementation((codes, market: Market) => ({
    quotes: codes.map((code) => ({
      code,
      name: null,
      industryCategory: null,
      price: 10,
      changePct: market === "US" ? 2.5 : null,
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

describe("SupplyChainGraph", () => {
  it("shows product, grade chip and as-of date on a peer", async () => {
    renderGraph([
      {
        ...base,
        product: "Precision gears",
        grade: "high",
        as_of: "2026-03-31",
      },
    ]);
    const link = await screen.findByRole("link", { name: /Beta Assembly 1002/ });
    expect(within(link).getByText("Precision gears")).toBeTruthy();
    expect(within(link).getByText(grade.high)).toBeTruthy();
    expect(within(link).getByText("截至 2026-03-31")).toBeTruthy();
  });

  it("falls back to the confidence grade and omits missing details", async () => {
    renderGraph([{ ...base, confidence: "low" }]);
    const link = await screen.findByRole("link", { name: /Beta Assembly 1002/ });
    expect(within(link).getByText(grade.low)).toBeTruthy();
    expect(link.textContent).not.toMatch(/截至/);
    expect(screen.queryByRole("button", { name: /資料來源/ })).toBeNull();
  });

  it("links a US peer with its own market and looks its quote up in the US", async () => {
    renderGraph([{ ...base, to_symbol: "ZED", to_market: "US", to_name: "Zed Corp" }]);
    const link = await screen.findByRole("link", { name: /Zed Corp ZED/ });
    expect(link.getAttribute("href")).toBe("/stock/ZED?market=US");
    expect(link.getAttribute("aria-label")).toContain("+2.50%");
    const usCall = vi
      .mocked(useSymbolQuotes)
      .mock.calls.filter(([, m]) => m === "US")
      .at(-1);
    expect(usCall?.[0]).toEqual(["ZED"]);
  });

  it("opens a keyboard-reachable list of source links that open safely in a new tab", async () => {
    renderGraph([
      {
        ...base,
        sources: [
          { publisher: "Example Wire", url: "https://example.test/a", published_on: "2026-03-01" },
          { publisher: "Sample Journal", url: "https://example.test/b" },
        ],
      },
    ]);
    const button = await screen.findByRole("button", { name: /Beta Assembly.*2/ });
    expect(button.tagName).toBe("BUTTON");
    fireEvent.click(button);
    const first = await screen.findByRole("link", { name: /Example Wire/ });
    expect(first.getAttribute("href")).toBe("https://example.test/a");
    expect(first.getAttribute("target")).toBe("_blank");
    expect(first.getAttribute("rel")).toBe("noopener noreferrer");
    expect(screen.getByRole("link", { name: /Sample Journal/ })).toBeTruthy();
  });

  it("keeps competitors in their own row", async () => {
    renderGraph([
      { ...base, edge_type: "competes", from_symbol: "1001", to_symbol: "1003", to_name: "Rival" },
    ]);
    expect(await screen.findByText(messages.stock.industry.supplyChainCompetitors)).toBeTruthy();
    expect(screen.getByRole("link", { name: /Rival 1003/ })).toBeTruthy();
  });

  it("states plainly when there is no relationship data", async () => {
    vi.mocked(getSupplyChainEdges).mockResolvedValue(respond([]));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    renderWithIntl(
      <QueryClientProvider client={client}>
        <SupplyChainGraph symbol="1001" market="TW" />
      </QueryClientProvider>,
    );
    expect(await screen.findByText(messages.stock.industry.supplyChainUnavailable)).toBeTruthy();
  });
});
