/**
 * Deterministic ring layout for the 關聯圖譜 ego graph. A pure function: the same nodes always
 * land on the same coordinates (no randomness, no physics), so the picture is stable between
 * renders, filter changes and reloads.
 *
 *   ring 0  the company in focus, at the centre
 *   ring 1  entities (technology, material, application, product)
 *   ring 2  peer companies (TW and international)
 *
 * Within a ring nodes are ordered by type, then weight (heaviest first), then label and id, and
 * spread evenly around the circle starting at the top and running clockwise. A ring grows beyond
 * its base radius when it would otherwise be too crowded for its nodes not to touch.
 */
import type { EntityLink, EntityNode, EntityType } from "@/types/entity-graph";

/** Angular order of types within a ring: entities first, then companies. */
export const ENTITY_TYPE_ORDER: readonly EntityType[] = [
  "technology",
  "material",
  "application",
  "product",
  "tw_company",
  "intl_company",
];

export type Ring = 0 | 1 | 2;

export type PlacedNode = {
  readonly node: EntityNode;
  readonly x: number;
  readonly y: number;
  /** Drawn radius, from the node's weight. */
  readonly r: number;
  readonly ring: Ring;
};

export type GraphLayout = {
  readonly nodes: readonly PlacedNode[];
  /** Radii of the guide rings that hold at least one node (ring 1 and/or ring 2). */
  readonly ringRadii: readonly number[];
  /** The SVG viewBox that contains every node and its label. */
  readonly viewBox: ViewBox;
};

export type ViewBox = {
  readonly x: number;
  readonly y: number;
  readonly w: number;
  readonly h: number;
};

const FOCUS_RADIUS = 24;
const MIN_NODE_RADIUS = 7;
const MAX_NODE_RADIUS = 17;
/** Clear space kept between neighbouring node edges on a ring, px. */
const NODE_GAP = 14;
const INNER_BASE = 120;
const OUTER_BASE = 215;
/** Minimum clear distance between the two guide rings, beyond the nodes' own radii, px. */
const RING_CLEARANCE = 46;
/** Horizontal room kept past the outer ring for labels, px. */
const LABEL_ROOM_X = 96;
const LABEL_ROOM_Y = 28;

const COMPANY_TYPES: ReadonlySet<EntityType> = new Set(["tw_company", "intl_company"]);

export function isCompanyType(type: EntityType): boolean {
  return COMPANY_TYPES.has(type);
}

/** Node radius grows with the square root of its weight so area, not width, tracks mentions. */
export function nodeRadius(weight: number, maxWeight: number): number {
  if (!(maxWeight > 0) || !(weight > 0)) {
    return MIN_NODE_RADIUS;
  }
  const share = Math.min(1, weight / maxWeight);
  return MIN_NODE_RADIUS + (MAX_NODE_RADIUS - MIN_NODE_RADIUS) * Math.sqrt(share);
}

function typeRank(type: EntityType): number {
  return ENTITY_TYPE_ORDER.indexOf(type);
}

/** Type, then weight (heaviest first), then label and id as final tie-breakers. */
export function compareNodes(a: EntityNode, b: EntityNode): number {
  return (
    typeRank(a.type) - typeRank(b.type) ||
    b.weight - a.weight ||
    a.label.localeCompare(b.label) ||
    a.id.localeCompare(b.id)
  );
}

type Sized = { readonly node: EntityNode; readonly r: number };

/** Radius a ring needs so `count` nodes of at most `maxR` fit side by side without touching. */
function crowdedRadius(count: number, maxR: number): number {
  return (count * (2 * maxR + NODE_GAP)) / (2 * Math.PI);
}

function placeOnRing(
  members: readonly Sized[],
  radius: number,
  ring: Ring,
  startAngle: number,
): PlacedNode[] {
  const step = (2 * Math.PI) / Math.max(1, members.length);
  return members.map((member, index) => {
    const angle = startAngle + index * step;
    return {
      node: member.node,
      r: member.r,
      ring,
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
    };
  });
}

function maxRadiusOf(members: readonly Sized[]): number {
  return members.reduce((max, member) => Math.max(max, member.r), 0);
}

export function layoutEntityGraph(nodes: readonly EntityNode[]): GraphLayout {
  const sorted = [...nodes].sort(compareNodes);
  const maxWeight = sorted.reduce((max, node) => Math.max(max, node.weight), 0);
  const sized = (node: EntityNode): Sized => ({ node, r: nodeRadius(node.weight, maxWeight) });

  // The centre is the focus company; any further node flagged `focus` is laid out as a peer.
  const centreNode = sorted
    .filter((node) => node.focus)
    .sort((a, b) => a.id.localeCompare(b.id))
    .at(0);
  const rest = sorted.filter((node) => node !== centreNode);
  const entities = rest.filter((node) => !isCompanyType(node.type)).map(sized);
  const companies = rest.filter((node) => isCompanyType(node.type)).map(sized);

  const placed: PlacedNode[] = [];
  if (centreNode) {
    placed.push({ node: centreNode, x: 0, y: 0, r: FOCUS_RADIUS, ring: 0 });
  }

  const innerMaxR = maxRadiusOf(entities);
  const innerRadius =
    entities.length === 0
      ? 0
      : Math.max(
          INNER_BASE,
          crowdedRadius(entities.length, innerMaxR),
          FOCUS_RADIUS + innerMaxR + 40,
        );
  placed.push(...placeOnRing(entities, innerRadius, 1, -Math.PI / 2));

  const outerMaxR = maxRadiusOf(companies);
  const outerFloor =
    entities.length === 0 ? 0 : innerRadius + innerMaxR + outerMaxR + RING_CLEARANCE;
  const outerRadius =
    companies.length === 0
      ? 0
      : Math.max(OUTER_BASE, outerFloor, crowdedRadius(companies.length, outerMaxR));
  // Half a step off the inner ring so the two rings do not line up spoke for spoke.
  const outerStart = -Math.PI / 2 + Math.PI / Math.max(1, companies.length);
  placed.push(...placeOnRing(companies, outerRadius, 2, outerStart));

  const ringRadii = [innerRadius, outerRadius].filter((radius) => radius > 0);
  const reach = Math.max(FOCUS_RADIUS, innerRadius + innerMaxR, outerRadius + outerMaxR);
  const halfW = reach + LABEL_ROOM_X;
  const halfH = reach + LABEL_ROOM_Y;
  return {
    nodes: placed,
    ringRadii,
    viewBox: { x: -halfW, y: -halfH, w: halfW * 2, h: halfH * 2 },
  };
}

/** A line between two nodes' edges (not their centres), so an arrowhead lands on the target's rim. */
export function linkEnds(
  from: PlacedNode,
  to: PlacedNode,
  pad = 2,
): { x1: number; y1: number; x2: number; y2: number } {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const distance = Math.hypot(dx, dy);
  if (distance === 0 || distance <= from.r + to.r + 2 * pad) {
    return { x1: from.x, y1: from.y, x2: to.x, y2: to.y };
  }
  const ux = dx / distance;
  const uy = dy / distance;
  return {
    x1: from.x + ux * (from.r + pad),
    y1: from.y + uy * (from.r + pad),
    x2: to.x - ux * (to.r + pad),
    y2: to.y - uy * (to.r + pad),
  };
}

/** Ids directly linked to `id` (either direction). */
export function neighbourIds(links: readonly EntityLink[], id: string): ReadonlySet<string> {
  const ids = new Set<string>();
  for (const link of links) {
    if (link.source === id) ids.add(link.target);
    if (link.target === id) ids.add(link.source);
  }
  return ids;
}
