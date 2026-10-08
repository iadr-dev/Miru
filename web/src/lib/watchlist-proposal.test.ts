import { describe, expect, it } from "vitest";
import {
  addAlreadyDone,
  assetClassOf,
  choiceReady,
  initialChoice,
  matchingGroups,
  parseWatchlistProposal,
  removalTargets,
  splitByClass,
  symbolsToAdd,
  type WatchlistProposal,
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
    id: `i-${id}-${symbol}`,
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

const add = (over: Partial<WatchlistProposal> = {}): WatchlistProposal => ({
  action: "add",
  market: "TW",
  symbols: ["2409", "3481"],
  ...over,
});

describe("parseWatchlistProposal", () => {
  it("accepts the backend payload and ignores the extra keys", () => {
    expect(
      parseWatchlistProposal({
        action: "add",
        market: "TW",
        symbols: ["2409", "3481"],
        group_name: null,
        requires_user_confirmation: true,
        next_step: "DONE",
      }),
    ).toEqual({ action: "add", market: "TW", symbols: ["2409", "3481"], group_name: null });
  });

  it("rejects a malformed proposal", () => {
    expect(parseWatchlistProposal({ action: "add", market: "TW", symbols: [] })).toBeNull();
    expect(parseWatchlistProposal({ action: "move", market: "TW", symbols: ["2330"] })).toBeNull();
    expect(parseWatchlistProposal({ action: "add", market: "XX", symbols: ["2330"] })).toBeNull();
    expect(parseWatchlistProposal({})).toBeNull();
  });
});

describe("classes and groups", () => {
  it("classifies by the code's shape and splits a mixed batch", () => {
    expect(assetClassOf("2330")).toBe("stock");
    expect(assetClassOf("0050")).toBe("etf");
    expect(splitByClass(["2330", "0050", "2409"])).toEqual([
      { assetClass: "stock", symbols: ["2330", "2409"] },
      { assetClass: "etf", symbols: ["0050"] },
    ]);
    expect(splitByClass(["0050"])).toEqual([{ assetClass: "etf", symbols: ["0050"] }]);
  });

  it("matches a group by market and class", () => {
    const groups = [
      group("tw-stock"),
      group("tw-etf", { asset_class: "etf" }),
      group("us-stock", { market: "US" }),
    ];
    expect(matchingGroups(groups, "TW", "etf").map((v) => v.group.id)).toEqual(["tw-etf"]);
    expect(matchingGroups(groups, "US", "stock").map((v) => v.group.id)).toEqual(["us-stock"]);
  });
});

describe("initialChoice", () => {
  const groups = [group("a"), group("b", { is_default: true }), group("面板")];

  it("uses a group the user named when it exists (any case), else proposes creating it", () => {
    expect(initialChoice(groups, add({ group_name: "面板" }), "stock")).toEqual({
      kind: "existing",
      id: "面板",
    });
    expect(initialChoice(groups, add({ group_name: "新族群" }), "stock")).toEqual({
      kind: "new",
      name: "新族群",
    });
  });

  it("otherwise prefers the default group, then the first, then a new unnamed one", () => {
    expect(initialChoice(groups, add(), "stock")).toEqual({ kind: "existing", id: "b" });
    expect(initialChoice([group("a")], add(), "stock")).toEqual({ kind: "existing", id: "a" });
    expect(initialChoice([], add(), "stock")).toEqual({ kind: "new", name: "" });
    expect(initialChoice(groups, add(), "etf")).toEqual({ kind: "new", name: "" });
  });
});

describe("choiceReady / symbolsToAdd", () => {
  it("needs an id for an existing group and a name for a new one", () => {
    expect(choiceReady({ kind: "existing", id: "a" })).toBe(true);
    expect(choiceReady({ kind: "existing", id: "" })).toBe(false);
    expect(choiceReady({ kind: "new", name: " 面板 " })).toBe(true);
    expect(choiceReady({ kind: "new", name: "  " })).toBe(false);
  });

  it("skips what the chosen group already holds, and adds everything to a new group", () => {
    const groups = [group("a", {}, ["2409"])];
    const batch = { assetClass: "stock" as const, symbols: ["2409", "3481"] };
    expect(symbolsToAdd(groups, { kind: "existing", id: "a" }, batch)).toEqual(["3481"]);
    expect(symbolsToAdd(groups, { kind: "new", name: "x" }, batch)).toEqual(["2409", "3481"]);
  });
});

describe("removal and 'already there'", () => {
  const groups = [group("a", {}, ["2330", "2409"]), group("b", {}, ["2330"])];

  it("lists, per symbol, the groups holding it (none = not in the watchlist)", () => {
    expect(
      removalTargets(groups, { action: "remove", market: "TW", symbols: ["2330", "9999"] }),
    ).toEqual([
      {
        symbol: "2330",
        groups: [
          { id: "a", name: "a" },
          { id: "b", name: "b" },
        ],
      },
      { symbol: "9999", groups: [] },
    ]);
  });

  it("treats an add as done only when every symbol is already somewhere", () => {
    expect(addAlreadyDone(groups, add({ symbols: ["2330", "2409"] }))).toBe(true);
    expect(addAlreadyDone(groups, add({ symbols: ["2330", "3481"] }))).toBe(false);
  });
});
