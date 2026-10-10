import type { SupplyChainEdge } from "@/types/stock-detail";

/** How a peer relates to the company in focus. */
export type PeerRole = "supplier" | "customer" | "competitor";

export type Peer = { symbol: string; name: string; role: PeerRole };

export type PeerGroups = Record<PeerRole, Peer[]>;

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

/** Group an edge list into suppliers / customers / competitors for one focus symbol, one entry per
 * peer per role, in first-seen order. */
export function groupPeers(edges: readonly SupplyChainEdge[], focus: string): PeerGroups {
  const groups: PeerGroups = { supplier: [], customer: [], competitor: [] };
  const seen = new Set<string>();
  for (const edge of edges) {
    const focusIsFrom = edge.from_symbol === focus;
    if (!focusIsFrom && edge.to_symbol !== focus) {
      continue;
    }
    const role = peerRole(edge, focus);
    const symbol = focusIsFrom ? edge.to_symbol : edge.from_symbol;
    const key = `${role}:${symbol}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    groups[role].push({ symbol, name: focusIsFrom ? edge.to_name : edge.from_name, role });
  }
  return groups;
}
