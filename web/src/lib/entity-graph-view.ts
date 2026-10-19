/**
 * Pure view-model helpers for the 關聯圖譜: weight/type filtering, name search, and the grouping the
 * list fallback uses. Kept out of the component so the rules are unit-tested without a DOM.
 */
import { ENTITY_TYPE_ORDER } from "@/lib/entity-graph-layout";
import type { EntityLink, EntityNode, EntityType } from "@/types/entity-graph";

export type GraphFilter = {
  /** Nodes lighter than this are hidden (the company in focus never is). */
  readonly minWeight: number;
  readonly hiddenTypes: ReadonlySet<EntityType>;
};

export type FilteredGraph = {
  readonly nodes: readonly EntityNode[];
  readonly links: readonly EntityLink[];
};

export function applyGraphFilter(
  nodes: readonly EntityNode[],
  links: readonly EntityLink[],
  filter: GraphFilter,
): FilteredGraph {
  const visible = (node: EntityNode): boolean =>
    node.focus || (node.weight >= filter.minWeight && !filter.hiddenTypes.has(node.type));
  const kept = nodes.filter(visible);
  const ids = new Set(kept.map((node) => node.id));
  return {
    nodes: kept,
    links: links.filter((link) => ids.has(link.source) && ids.has(link.target)),
  };
}

export function maxNodeWeight(nodes: readonly EntityNode[]): number {
  return nodes.reduce((max, node) => (node.focus ? max : Math.max(max, node.weight)), 0);
}

/** Ids whose label contains `query` (case-insensitive, trimmed). An empty query matches nothing. */
export function matchingNodeIds(nodes: readonly EntityNode[], query: string): ReadonlySet<string> {
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) {
    return new Set();
  }
  return new Set(
    nodes
      .filter(
        (node) =>
          node.label.toLowerCase().includes(needle) ||
          (node.symbol ?? "").toLowerCase().startsWith(needle),
      )
      .map((node) => node.id),
  );
}

export type TypeGroup = {
  readonly type: EntityType;
  readonly nodes: readonly EntityNode[];
};

/** Non-empty groups in the fixed type order, each heaviest first. The focus node is excluded. */
export function groupByType(nodes: readonly EntityNode[]): readonly TypeGroup[] {
  return ENTITY_TYPE_ORDER.map((type) => ({
    type,
    nodes: nodes
      .filter((node) => !node.focus && node.type === type)
      .sort((a, b) => b.weight - a.weight || a.label.localeCompare(b.label)),
  })).filter((group) => group.nodes.length > 0);
}

/** The mark drawn inside a company node: its first character (a CJK name's first glyph, a Latin
 * name's initial). A badge, not a logo - no image is fetched, so nothing leaves our origin. */
export function monogram(label: string): string {
  const first = Array.from(label.trim())[0];
  return first === undefined ? "" : first.toUpperCase();
}

/** `/stock/{symbol}?market=…` for a company node, or null when it has no symbol. */
export function companyHref(node: EntityNode): string | null {
  if (node.symbol === null || node.market === null) {
    return null;
  }
  return `/stock/${encodeURIComponent(node.symbol)}?market=${node.market}`;
}

type ThemeRef = { readonly key: string; readonly name_zh: string; readonly name_en: string };

/** The concept theme an entity points at: a theme whose name or key equals the entity label. */
export function themeKeyForEntity(label: string, themes: readonly ThemeRef[]): string | null {
  const needle = label.trim().toLowerCase();
  if (needle.length === 0) {
    return null;
  }
  const hit = themes.find(
    (theme) =>
      theme.name_zh.trim().toLowerCase() === needle ||
      theme.key.toLowerCase() === needle ||
      theme.name_en.trim().toLowerCase() === needle,
  );
  return hit?.key ?? null;
}
