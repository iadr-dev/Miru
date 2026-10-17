import { safeHttpUrl } from "@/lib/safe-url";
import type { Market } from "@/types/settings";
import type { EdgeConfidence, SupplyChainEdge, SupplyChainSource } from "@/types/stock/profile";

/** How a peer relates to the company in focus. */
export type PeerRole = "supplier" | "customer" | "competitor";

export type Peer = {
  symbol: string;
  /** Peers are keyed by (symbol, market): the same code can exist in both markets. */
  market: Market;
  name: string;
  role: PeerRole;
  grade: EdgeConfidence;
  product: string | null;
  asOf: string | null;
  revenuePct: number | null;
  /** Only http(s) citations; anything else is dropped before it can become a link. */
  sources: readonly SupplyChainSource[];
};

export type PeerGroups = Record<PeerRole, Peer[]>;

/** Stable identity of a peer: `market:symbol`. */
export function peerKey(peer: Pick<Peer, "symbol" | "market">): string {
  return `${peer.market}:${peer.symbol}`;
}

/** Guess a market from a code's shape (Taiwan codes start with a digit). Only a fallback for
 * payloads that do not carry `from_market` / `to_market`. */
function guessMarket(code: string): Market {
  return /^\d/.test(code) ? "TW" : "US";
}

type Side = "from" | "to";

function marketOf(edge: SupplyChainEdge, side: Side): Market {
  const explicit = side === "from" ? edge.from_market : edge.to_market;
  return explicit ?? guessMarket(side === "from" ? edge.from_symbol : edge.to_symbol);
}

function isFocus(edge: SupplyChainEdge, side: Side, focus: string, focusMarket?: Market): boolean {
  const symbol = side === "from" ? edge.from_symbol : edge.to_symbol;
  if (symbol !== focus) {
    return false;
  }
  return focusMarket === undefined || marketOf(edge, side) === focusMarket;
}

/** The role of the *other* company in an edge, seen from the company in focus.
 *
 * `supplies` is "from supplies to" and `customer` is "to is a customer of from" - the same
 * direction. So when the focus is the `from` side, the peer is its customer; when the focus is the
 * `to` side, the peer is its supplier. `competes` has no direction. (Reading the edge type
 * verbatim, as the first version did, labelled TSMC's customers "供應".) */
export function peerRole(edge: SupplyChainEdge, focus: string): PeerRole {
  if (edge.edge_type === "competes") {
    return "competitor";
  }
  return edge.from_symbol === focus ? "customer" : "supplier";
}

function linkableSources(sources: readonly SupplyChainSource[]): SupplyChainSource[] {
  return sources.filter((source) => safeHttpUrl(source.url) !== null);
}

/** Group an edge list into suppliers / customers / competitors for one focus symbol, one entry per
 * peer per role, in first-seen order. Pass `focusMarket` so a same-coded company in the other
 * market is not mistaken for the focus. */
export function groupPeers(
  edges: readonly SupplyChainEdge[],
  focus: string,
  focusMarket?: Market,
): PeerGroups {
  const groups: PeerGroups = { supplier: [], customer: [], competitor: [] };
  const seen = new Set<string>();
  for (const edge of edges) {
    const focusIsFrom = isFocus(edge, "from", focus, focusMarket);
    if (!focusIsFrom && !isFocus(edge, "to", focus, focusMarket)) {
      continue;
    }
    const role = peerRole(edge, focus);
    const peerSide: Side = focusIsFrom ? "to" : "from";
    const symbol = focusIsFrom ? edge.to_symbol : edge.from_symbol;
    const market = marketOf(edge, peerSide);
    const key = `${role}:${market}:${symbol}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    const product = edge.product?.trim();
    groups[role].push({
      symbol,
      market,
      name: focusIsFrom ? edge.to_name : edge.from_name,
      role,
      grade: edge.grade ?? edge.confidence,
      product: product ? product : null,
      asOf: edge.as_of ?? null,
      revenuePct: edge.revenue_pct ?? null,
      sources: linkableSources(edge.sources),
    });
  }
  return groups;
}
