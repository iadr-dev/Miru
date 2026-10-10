import { describe, expect, it } from "vitest";
import { groupPeers, peerRole } from "@/lib/supply-chain";
import type { SupplyChainEdge } from "@/types/stock-detail";

const edge = (from: string, type: SupplyChainEdge["edge_type"], to: string): SupplyChainEdge => ({
  from_symbol: from,
  from_name: `n${from}`,
  to_symbol: to,
  to_name: `n${to}`,
  edge_type: type,
  confidence: "high",
  market: "TW",
  source: "seed",
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
  it("splits TSMC's real edges into customers, suppliers and competitors", () => {
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
