import { z } from "zod";
import { classifySymbolType, isEtfSymbolType } from "@/lib/symbol-type";
import { MARKETS } from "@/types/settings";
import type { WatchlistAssetClass, WatchlistGroupView } from "@/types/watchlist";

/** The `watchlist_proposal` rich card the agent's `propose_watchlist_change` tool emits
 * (backend/src/tools/propose_watchlist_tools.py). The agent only *proposes*: the user checks the
 * names, picks the group and taps the button, and the frontend calls the ordinary watchlist routes.
 * Everything that decides what will happen lives here as pure functions, so it is unit-tested. */
export const watchlistProposalSchema = z.object({
  action: z.enum(["add", "remove"]),
  market: z.enum(MARKETS),
  symbols: z.array(z.string().min(1).max(16)).min(1).max(10),
  group_name: z.string().max(30).nullable().optional(),
});
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
