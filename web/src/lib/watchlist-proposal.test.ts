import { describe, expect, it } from "vitest";
import {
  defaultGroupId,
  groupHolding,
  groupsForProposal,
  parseWatchlistProposal,
} from "@/lib/watchlist-proposal";
import type { WatchlistGroupView } from "@/types/watchlist";

const group = (
  id: string,
  over: Partial<WatchlistGroupView["group"]> = {},
  symbols: string[] = [],
): WatchlistGroupView => ({
  group: {
    id,
    user_id: "u",
    name: id,
    market: "TW",
    asset_class: "stock",
    sort_order: "a",
    is_default: false,
    version: 1,
    created_at: "2026-10-08T00:00:00",
    updated_at: "2026-10-08T00:00:00",
    ...over,
  },
  items: symbols.map((symbol) => ({
    id: `i-${symbol}`,
    watchlist_id: id,
    user_id: "u",
    symbol,
    market: "TW",
    asset_class: "stock",
    note: null,
    sort_order: "a",
    added_at: "2026-10-08T00:00:00",
  })),
});

describe("parseWatchlistProposal", () => {
  it("accepts the backend payload and ignores the extra keys", () => {
    expect(
      parseWatchlistProposal({
        symbol: "2330",
        market: "TW",
        requires_user_confirmation: true,
        next_step: "DONE",
      }),
    ).toEqual({ symbol: "2330", market: "TW" });
  });

  it("rejects a malformed proposal", () => {
    expect(parseWatchlistProposal({ symbol: "", market: "TW" })).toBeNull();
    expect(parseWatchlistProposal({ symbol: "2330", market: "XX" })).toBeNull();
  });
});

describe("groupsForProposal", () => {
  const groups = [
    group("tw-stock"),
    group("tw-etf", { asset_class: "etf" }),
    group("us-stock", { market: "US" }),
  ];

  it("puts an equity in a stock group of its market", () => {
    expect(
      groupsForProposal(groups, { symbol: "2330", market: "TW" }).map((g) => g.group.id),
    ).toEqual(["tw-stock"]);
  });

  it("puts an ETF code in an ETF group", () => {
    expect(
      groupsForProposal(groups, { symbol: "0050", market: "TW" }).map((g) => g.group.id),
    ).toEqual(["tw-etf"]);
  });

  it("is empty when the market has no matching group", () => {
    expect(
      groupsForProposal(groups, { symbol: "AAPL", market: "US" }).map((g) => g.group.id),
    ).toEqual(["us-stock"]);
    expect(groupsForProposal([], { symbol: "2330", market: "TW" })).toEqual([]);
  });
});

describe("defaultGroupId / groupHolding", () => {
  it("prefers the default group, else the first, else empty", () => {
    expect(defaultGroupId([group("a"), group("b", { is_default: true })])).toBe("b");
    expect(defaultGroupId([group("a"), group("b")])).toBe("a");
    expect(defaultGroupId([])).toBe("");
  });

  it("finds a group that already holds the symbol", () => {
    const groups = [group("a", {}, ["2317"]), group("b", {}, ["2330"])];
    expect(groupHolding(groups, { symbol: "2330", market: "TW" })?.group.id).toBe("b");
    expect(groupHolding(groups, { symbol: "2454", market: "TW" })).toBeNull();
  });
});
