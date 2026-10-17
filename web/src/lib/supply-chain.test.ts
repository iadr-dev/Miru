import { describe, expect, it } from "vitest";
import { groupPeers, peerRole } from "@/lib/supply-chain";
import type { SupplyChainEdge } from "@/types/stock/profile";

const edge = (from: string, type: SupplyChainEdge["edge_type"], to: string): SupplyChainEdge => ({
  from_symbol: from,
  from_name: `n${from}`,
  to_symbol: to,
  to_name: `n${to}`,
  edge_type: type,
  confidence: "high",
  market: "TW",
  source: "seed",
  sources: [],
});

describe("peerRole", () => {
  it("reads direction from the focus's side of the edge", () => {
    expect(peerRole(edge("2330", "supplies", "AAPL"), "2330")).toBe("customer");
    expect(peerRole(edge("3413", "supplies", "2330"), "2330")).toBe("supplier");
    expect(peerRole(edge("2330", "customer", "AAPL"), "2330")).toBe("customer");
    expect(peerRole(edge("3413", "customer", "2330"), "2330")).toBe("supplier");
    expect(peerRole(edge("2303", "competes", "2330"), "2330")).toBe("competitor");
    expect(peerRole(edge("2330", "competes", "2303"), "2330")).toBe("competitor");
  });
});

describe("groupPeers", () => {
  it("splits an example hub's edges into customers, suppliers and competitors", () => {
    const groups = groupPeers(
      [
        edge("2330", "supplies", "AAPL"),
        edge("2330", "supplies", "NVDA"),
        edge("2303", "competes", "2330"),
        edge("3413", "supplies", "2330"),
      ],
      "2330",
    );
    expect(groups.customer.map((p) => p.symbol)).toEqual(["AAPL", "NVDA"]);
    expect(groups.supplier.map((p) => p.symbol)).toEqual(["3413"]);
    expect(groups.competitor.map((p) => p.symbol)).toEqual(["2303"]);
  });

  it("lists a peer once per role and ignores edges that do not involve the focus", () => {
    const groups = groupPeers(
      [
        edge("2330", "supplies", "AAPL"),
        edge("2330", "customer", "AAPL"),
        edge("1111", "supplies", "2222"),
      ],
      "2330",
    );
    expect(groups.customer).toHaveLength(1);
    expect(groups.supplier).toHaveLength(0);
  });
});

describe("groupPeers peer details", () => {
  const rich: SupplyChainEdge = {
    ...edge("1001", "supplies", "ZZZ"),
    to_market: "US",
    from_market: "TW",
    confidence: "low",
    grade: "high",
    product: "  Widgets  ",
    as_of: "2026-03-31",
    revenue_pct: 12,
    sources: [
      { publisher: "Example Wire", url: "https://example.test/a", published_on: "2026-03-01" },
      { publisher: "Bad", url: "javascript:alert(1)" },
      { publisher: "Not a URL", url: "nope" },
    ],
  };

  it("carries market, grade, product, as-of, revenue share and only http(s) sources", () => {
    const [peer] = groupPeers([rich], "1001", "TW").customer;
    expect(peer).toMatchObject({
      symbol: "ZZZ",
      market: "US",
      grade: "high",
      product: "Widgets",
      asOf: "2026-03-31",
      revenuePct: 12,
    });
    expect(peer?.sources.map((s) => s.publisher)).toEqual(["Example Wire"]);
  });

  it("falls back to confidence, a symbol-shape market and empty details for an old payload", () => {
    const [peer] = groupPeers([edge("1001", "supplies", "ZZZ")], "1001").customer;
    expect(peer).toMatchObject({
      market: "US",
      grade: "high",
      product: null,
      asOf: null,
      revenuePct: null,
      sources: [],
    });
  });

  it("keeps same-coded peers from different markets apart and ignores a same-coded focus", () => {
    const twTwin = { ...edge("1001", "supplies", "5000"), to_market: "TW" as const };
    const usTwin = { ...edge("1001", "supplies", "5000"), to_market: "US" as const };
    const otherFocus = {
      ...edge("1001", "supplies", "7000"),
      from_market: "US" as const,
      to_market: "US" as const,
    };
    const groups = groupPeers([twTwin, usTwin, otherFocus], "1001", "TW");
    expect(groups.customer.map((p) => `${p.market}:${p.symbol}`)).toEqual(["TW:5000", "US:5000"]);
  });
});
