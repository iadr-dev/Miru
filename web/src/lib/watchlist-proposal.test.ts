import { describe, expect, it } from "vitest";
import {
  addAlreadyDone,
  assetClassOf,
  choiceReady,
  findGroups,
  initialChoice,
  isGroupAction,
  matchingGroups,
  movePlan,
  parseWatchlistProposal,
  removalTargets,
  reorderPlan,
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

describe("group actions", () => {
  it("parses the group-level proposals", () => {
    expect(
      parseWatchlistProposal({
        action: "rename_group",
        market: "TW",
        symbols: [],
        group_name: "面板",
        new_name: "顯示器",
        position: null,
      }),
    ).toMatchObject({ action: "rename_group", group_name: "面板", new_name: "顯示器" });
    expect(
      parseWatchlistProposal({
        action: "reorder_group",
        market: "TW",
        group_name: "a",
        position: "first",
      })?.symbols,
    ).toEqual([]);
    expect(
      parseWatchlistProposal({ action: "reorder_group", market: "TW", position: "middle" }),
    ).toBeNull();
  });

  it("knows which actions act on a group", () => {
    expect(isGroupAction("delete_group")).toBe(true);
    expect(isGroupAction("move")).toBe(false);
    expect(isGroupAction("add")).toBe(false);
  });

  it("finds groups by name, ignoring case and spacing", () => {
    const groups = [group("a", { name: "Semis" }), group("b", { name: "面板" })];
    expect(findGroups(groups, "TW", " semis ").map((v) => v.group.id)).toEqual(["a"]);
    expect(findGroups(groups, "TW", "none")).toEqual([]);
    expect(findGroups(groups, "US", "面板")).toEqual([]);
    expect(findGroups(groups, "TW", null)).toEqual([]);
  });
});

describe("movePlan", () => {
  const groups = [
    group("a", {}, ["2330", "2409"]),
    group("b", {}, ["3481"]),
    group("e", { asset_class: "etf" }, ["0050"]),
  ];
  const target = groups[1];
  const move = (symbols: string[]): WatchlistProposal => ({
    action: "move",
    market: "TW",
    symbols,
    group_name: "b",
  });

  it("moves symbols from the group holding them", () => {
    if (!target) throw new Error("fixture");
    const plan = movePlan(groups, move(["2330", "2409"]), target);
    expect(plan.steps.map((s) => [s.symbol, s.fromId])).toEqual([
      ["2330", "a"],
      ["2409", "a"],
    ]);
    expect(plan.skipped).toEqual([]);
  });

  it("skips what is already there, not in the watchlist, or of the wrong class", () => {
    if (!target) throw new Error("fixture");
    const plan = movePlan(groups, move(["3481", "9999", "0050"]), target);
    expect(plan.steps).toEqual([]);
    expect(plan.skipped).toEqual([
      { symbol: "3481", reason: "already" },
      { symbol: "9999", reason: "notIn" },
      { symbol: "0050", reason: "classMismatch" },
    ]);
  });
});

describe("reorderPlan", () => {
  const groups = [group("a"), group("b"), group("c"), group("e", { asset_class: "etf" })];
  const [a, b, c, e] = groups;

  it("puts a group before the first sibling or after the last", () => {
    if (!a || !b || !c) throw new Error("fixture");
    expect(reorderPlan(groups, c, "first")).toEqual({ noop: false, afterId: null, beforeId: "a" });
    expect(reorderPlan(groups, a, "last")).toEqual({ noop: false, afterId: "c", beforeId: null });
    expect(reorderPlan(groups, b, "first").beforeId).toBe("a");
  });

  it("does nothing when it is already there, and only compares groups of its own class", () => {
    if (!a || !c || !e) throw new Error("fixture");
    expect(reorderPlan(groups, a, "first").noop).toBe(true);
    expect(reorderPlan(groups, c, "last").noop).toBe(true);
    expect(reorderPlan(groups, e, "first").noop).toBe(true);
  });
});
