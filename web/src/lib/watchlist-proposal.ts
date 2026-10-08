import { z } from "zod";
import { classifySymbolType, isEtfSymbolType } from "@/lib/symbol-type";
import { MARKETS } from "@/types/settings";
import type { WatchlistGroupView } from "@/types/watchlist";

/** The `watchlist_proposal` rich card the agent's `propose_watchlist_add` tool emits
 * (backend/src/tools/propose_watchlist_tools.py). The agent only *proposes*: the user picks a group
 * and taps 加入, and the frontend calls the ordinary `PUT /watchlists/{id}/items/{symbol}`. */
export const watchlistProposalSchema = z.object({
  symbol: z.string().min(1).max(16),
  market: z.enum(MARKETS),
});
export type WatchlistProposal = z.infer<typeof watchlistProposalSchema>;

/** A malformed proposal yields null so the card renders nothing rather than a button that fails. */
export function parseWatchlistProposal(data: Record<string, unknown>): WatchlistProposal | null {
  const parsed = watchlistProposalSchema.safeParse(data);
  return parsed.success ? parsed.data : null;
}

/** The groups this symbol can go into: same market, and the stock/ETF class its code implies (an
 * ETF code belongs in an ETF group, a plain equity in a stock group). */
export function groupsForProposal(
  groups: readonly WatchlistGroupView[],
  proposal: WatchlistProposal,
): WatchlistGroupView[] {
  const wanted = isEtfSymbolType(classifySymbolType(proposal.symbol)) ? "etf" : "stock";
  return groups.filter(
    (view) => view.group.market === proposal.market && view.group.asset_class === wanted,
  );
}

/** The group a card pre-selects: the user's default group if there is one, else the first. */
export function defaultGroupId(candidates: readonly WatchlistGroupView[]): string {
  return (candidates.find((view) => view.group.is_default) ?? candidates[0])?.group.id ?? "";
}

/** The first group already holding the symbol, so a card re-rendered from history (or tapped twice)
 * says "already in your watchlist" instead of adding again. */
export function groupHolding(
  groups: readonly WatchlistGroupView[],
  proposal: WatchlistProposal,
): WatchlistGroupView | null {
  return (
    groups.find(
      (view) =>
        view.group.market === proposal.market &&
        view.items.some((item) => item.symbol === proposal.symbol),
    ) ?? null
  );
}
