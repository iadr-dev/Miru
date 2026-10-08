import { z } from "zod";
import { classifySymbolType, isEtfSymbolType } from "@/lib/symbol-type";
import { MARKETS } from "@/types/settings";
import type { WatchlistAssetClass, WatchlistGroupView } from "@/types/watchlist";

/** The `watchlist_proposal` rich card the agent's `propose_watchlist_change` tool emits
 * (backend/src/tools/propose_watchlist_tools.py). The agent only *proposes*: the user checks the
 * names, picks the group and taps the button, and the frontend calls the ordinary watchlist routes.
 * Everything that decides what will happen lives here as pure functions, so it is unit-tested. */
export const WATCHLIST_ACTIONS = [
  "add",
  "remove",
  "move",
  "rename_group",
  "delete_group",
  "reorder_group",
] as const;
export type WatchlistAction = (typeof WATCHLIST_ACTIONS)[number];

export const watchlistProposalSchema = z
  .object({
    action: z.enum(WATCHLIST_ACTIONS),
    market: z.enum(MARKETS),
    symbols: z.array(z.string().min(1).max(16)).max(10).default([]),
    group_name: z.string().max(30).nullable().optional(),
    new_name: z.string().max(30).nullable().optional(),
    position: z.enum(["first", "last"]).nullable().optional(),
  })
  .refine(
    // The backend enforces the same shape per action; this keeps a malformed card from rendering a
    // button that cannot do what it says.
    (p) => {
      const needsSymbols = p.action === "add" || p.action === "remove" || p.action === "move";
      if (needsSymbols !== p.symbols.length > 0) return false;
      if (p.action === "move" || p.action.endsWith("_group")) return Boolean(p.group_name);
      return true;
    },
  );
export type WatchlistProposal = z.infer<typeof watchlistProposalSchema>;

/** A malformed proposal yields null so the card renders nothing rather than a button that fails. */
export function parseWatchlistProposal(data: Record<string, unknown>): WatchlistProposal | null {
  const parsed = watchlistProposalSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

export function assetClassOf(symbol: string): WatchlistAssetClass {
  return isEtfSymbolType(classifySymbolType(symbol)) ? "etf" : "stock";
}

export type SymbolBatch = { assetClass: WatchlistAssetClass; symbols: string[] };

/** The symbols split by the stock/ETF class their code implies (stock first). A stock group and an
 * ETF group are different groups, so one proposal can need up to two group choices. */
export function splitByClass(symbols: readonly string[]): SymbolBatch[] {
  const batches: SymbolBatch[] = [
    { assetClass: "stock", symbols: symbols.filter((s) => assetClassOf(s) === "stock") },
    { assetClass: "etf", symbols: symbols.filter((s) => assetClassOf(s) === "etf") },
  ];
  return batches.filter((batch) => batch.symbols.length > 0);
}

/** The user's groups a symbol of this market and class can go into. */
export function matchingGroups(
  groups: readonly WatchlistGroupView[],
  market: WatchlistProposal["market"],
  assetClass: WatchlistAssetClass,
): WatchlistGroupView[] {
  return groups.filter((v) => v.group.market === market && v.group.asset_class === assetClass);
}

/** Where a batch goes: one of the user's groups, or a new group to create first. */
export type GroupChoice = { kind: "existing"; id: string } | { kind: "new"; name: string };

/** The choice a card starts with: the group the user named (an existing one, else a new one with
 * that name), else their default group, else the first, else "a new group" with the name left
 * for the user to type. */
export function initialChoice(
  groups: readonly WatchlistGroupView[],
  proposal: WatchlistProposal,
  assetClass: WatchlistAssetClass,
): GroupChoice {
  const candidates = matchingGroups(groups, proposal.market, assetClass);
  const wanted = proposal.group_name?.trim();
  if (wanted) {
    const same = candidates.find((v) => v.group.name.toLowerCase() === wanted.toLowerCase());
    return same ? { kind: "existing", id: same.group.id } : { kind: "new", name: wanted };
  }
  const pick = candidates.find((v) => v.group.is_default) ?? candidates[0];
  return pick ? { kind: "existing", id: pick.group.id } : { kind: "new", name: "" };
}

/** Can this choice be carried out? A new group needs a name. */
export function choiceReady(choice: GroupChoice): boolean {
  return choice.kind === "existing" ? choice.id !== "" : choice.name.trim() !== "";
}

/** The symbols of a batch not already in the chosen existing group (a new group holds none), so a
 * repeat tap never re-adds. */
export function symbolsToAdd(
  groups: readonly WatchlistGroupView[],
  choice: GroupChoice,
  batch: SymbolBatch,
): string[] {
  if (choice.kind === "new") {
    return batch.symbols;
  }
  const held = new Set(
    groups.find((v) => v.group.id === choice.id)?.items.map((item) => item.symbol) ?? [],
  );
  return batch.symbols.filter((symbol) => !held.has(symbol));
}

export type RemovalTarget = {
  symbol: string;
  groups: { id: string; name: string }[];
};

/** For a remove proposal: every requested symbol with the groups (in its market) that hold it. A
 * symbol with no groups is not in the watchlist at all. */
export function removalTargets(
  groups: readonly WatchlistGroupView[],
  proposal: WatchlistProposal,
): RemovalTarget[] {
  return proposal.symbols.map((symbol) => ({
    symbol,
    groups: groups
      .filter(
        (v) => v.group.market === proposal.market && v.items.some((item) => item.symbol === symbol),
      )
      .map((v) => ({ id: v.group.id, name: v.group.name })),
  }));
}

/** An add proposal is already satisfied when every symbol is in at least one group of its market
 * (so a card re-rendered from history says so instead of offering the button again). */
export function addAlreadyDone(
  groups: readonly WatchlistGroupView[],
  proposal: WatchlistProposal,
): boolean {
  return removalTargets(groups, proposal).every((target) => target.groups.length > 0);
}

/** The actions that act on a group itself rather than on symbols. */
export function isGroupAction(action: WatchlistAction): boolean {
  return action === "rename_group" || action === "delete_group" || action === "reorder_group";
}

/** The user's groups in this market whose name matches (case-insensitively). More than one means
 * the same name is used for a stock group and an ETF group, and the card asks which. */
export function findGroups(
  groups: readonly WatchlistGroupView[],
  market: WatchlistProposal["market"],
  name: string | null | undefined,
): WatchlistGroupView[] {
  const wanted = name?.trim().toLowerCase();
  if (!wanted) {
    return [];
  }
  return groups.filter(
    (v) => v.group.market === market && v.group.name.trim().toLowerCase() === wanted,
  );
}

export type MoveStep = { symbol: string; itemId: string; fromId: string; fromName: string };
export type MoveSkip = { symbol: string; reason: "notIn" | "already" | "classMismatch" };

/** What a move to `target` will do: each requested symbol that is in the user's watchlist (and not
 * already in `target`) moves from the first group holding it. A symbol of the wrong class for the
 * target (an ETF into a stock group) or one not in the watchlist at all is skipped, with the reason
 * shown on the card. */
export function movePlan(
  groups: readonly WatchlistGroupView[],
  proposal: WatchlistProposal,
  target: WatchlistGroupView,
): { steps: MoveStep[]; skipped: MoveSkip[] } {
  const steps: MoveStep[] = [];
  const skipped: MoveSkip[] = [];
  for (const symbol of proposal.symbols) {
    if (assetClassOf(symbol) !== target.group.asset_class) {
      skipped.push({ symbol, reason: "classMismatch" });
      continue;
    }
    if (target.items.some((item) => item.symbol === symbol)) {
      skipped.push({ symbol, reason: "already" });
      continue;
    }
    const source = groups.find(
      (v) =>
        v.group.market === proposal.market &&
        v.group.id !== target.group.id &&
        v.items.some((item) => item.symbol === symbol),
    );
    const item = source?.items.find((i) => i.symbol === symbol);
    if (!source || !item) {
      skipped.push({ symbol, reason: "notIn" });
      continue;
    }
    steps.push({ symbol, itemId: item.id, fromId: source.group.id, fromName: source.group.name });
  }
  return { steps, skipped };
}

export type ReorderPlan = { noop: boolean; afterId: string | null; beforeId: string | null };

/** The neighbours that put `view` first or last among the groups of its own market and class (the
 * order the sidebar shows). Already there means nothing to do. */
export function reorderPlan(
  groups: readonly WatchlistGroupView[],
  view: WatchlistGroupView,
  position: "first" | "last",
): ReorderPlan {
  const siblings = groups.filter(
    (v) => v.group.market === view.group.market && v.group.asset_class === view.group.asset_class,
  );
  const edge = position === "first" ? siblings.at(0) : siblings.at(-1);
  if (!edge || edge.group.id === view.group.id) {
    return { noop: true, afterId: null, beforeId: null };
  }
  return position === "first"
    ? { noop: false, afterId: null, beforeId: edge.group.id }
    : { noop: false, afterId: edge.group.id, beforeId: null };
}
