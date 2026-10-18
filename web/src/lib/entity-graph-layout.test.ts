import { describe, expect, it } from "vitest";
import { layoutEntityGraph, linkEnds, neighbourIds, nodeRadius } from "@/lib/entity-graph-layout";
import type { EntityLink, EntityNode, EntityType } from "@/types/entity-graph";

// Invented companies and entities only.
function node(id: string, type: EntityType, weight: number, focus = false): EntityNode {
  const isCompany = type === "tw_company" || type === "intl_company";
  return {
    id,
    label: `Label ${id}`,
    type,
    symbol: isCompany ? id : null,
    market: isCompany ? "TW" : null,
    weight,
    focus,
  };
}

function sample(): EntityNode[] {
  return [
    node("9001", "tw_company", 10, true),
    node("e-laser", "technology", 8),
    node("e-film", "material", 5),
    node("e-lens", "product", 3),
    node("e-cam", "application", 6),
    node("9002", "tw_company", 4),
    node("9003", "tw_company", 7),
    node("A1", "intl_company", 2),
  ];
}

function many(count: number): EntityNode[] {
  const out: EntityNode[] = [node("9001", "tw_company", 9, true)];
  for (let i = 0; i < count; i++) {
    out.push(node(`e${i}`, "technology", (i % 7) + 1));
    out.push(node(`c${i}`, "tw_company", (i % 5) + 1));
  }
  return out;
}

describe("layoutEntityGraph", () => {
  it("is deterministic regardless of input order", () => {
    const a = layoutEntityGraph(sample());
    const b = layoutEntityGraph([...sample()].reverse());
    const flat = (l: ReturnType<typeof layoutEntityGraph>) =>
      [...l.nodes]
        .sort((x, y) => x.node.id.localeCompare(y.node.id))
        .map((p) => [p.node.id, p.x, p.y, p.r, p.ring]);
    expect(flat(a)).toEqual(flat(b));
    expect(layoutEntityGraph(sample())).toEqual(layoutEntityGraph(sample()));
  });

  it("puts the focus node in the centre, entities on ring 1 and companies on ring 2", () => {
    const layout = layoutEntityGraph(sample());
    const byId = new Map(layout.nodes.map((p) => [p.node.id, p]));
    expect(byId.get("9001")).toMatchObject({ x: 0, y: 0, ring: 0 });
    for (const id of ["e-laser", "e-film", "e-lens", "e-cam"]) {
      expect(byId.get(id)?.ring).toBe(1);
    }
    for (const id of ["9002", "9003", "A1"]) {
      expect(byId.get(id)?.ring).toBe(2);
    }
    const dist = (id: string) => Math.hypot(byId.get(id)?.x ?? 0, byId.get(id)?.y ?? 0);
    expect(dist("e-laser")).toBeCloseTo(dist("e-film"), 6);
    expect(dist("9002")).toBeCloseTo(dist("9003"), 6);
    expect(dist("9002")).toBeGreaterThan(dist("e-laser"));
  });

  it("orders a ring by type, then weight, starting at the top and running clockwise", () => {
    const layout = layoutEntityGraph(sample());
    const ring1 = layout.nodes.filter((p) => p.ring === 1);
    // technology, material, application, product -> the order of the ring's angles.
    expect(ring1.map((p) => p.node.id)).toEqual(["e-laser", "e-film", "e-cam", "e-lens"]);
    expect(ring1[0]?.x).toBeCloseTo(0, 6);
    expect(ring1[0]?.y).toBeLessThan(0);
    const ring2 = layout.nodes.filter((p) => p.ring === 2).map((p) => p.node.id);
    // tw companies by weight desc, then international.
    expect(ring2).toEqual(["9003", "9002", "A1"]);
  });

  it("scales a node's radius with its weight", () => {
    expect(nodeRadius(0, 10)).toBe(nodeRadius(-3, 10));
    expect(nodeRadius(10, 10)).toBeGreaterThan(nodeRadius(2, 10));
    expect(nodeRadius(5, 0)).toBe(nodeRadius(0, 10));
    expect(nodeRadius(50, 10)).toBe(nodeRadius(10, 10));
  });

  it.each([3, 12, 30])("keeps %i-per-ring nodes from overlapping (within 1px)", (count) => {
    const layout = layoutEntityGraph(many(count));
    const placed = layout.nodes;
    for (let i = 0; i < placed.length; i++) {
      for (let j = i + 1; j < placed.length; j++) {
        const a = placed[i];
        const b = placed[j];
        if (!a || !b) continue;
        const gap = Math.hypot(a.x - b.x, a.y - b.y) - (a.r + b.r);
        expect(gap).toBeGreaterThan(-1);
      }
    }
  });

  it("keeps every node inside the view box", () => {
    const layout = layoutEntityGraph(many(30));
    const { x, y, w, h } = layout.viewBox;
    for (const p of layout.nodes) {
      expect(p.x - p.r).toBeGreaterThanOrEqual(x);
      expect(p.x + p.r).toBeLessThanOrEqual(x + w);
      expect(p.y - p.r).toBeGreaterThanOrEqual(y);
      expect(p.y + p.r).toBeLessThanOrEqual(y + h);
    }
  });

  it("handles an empty graph and a graph with only the focus node", () => {
    expect(layoutEntityGraph([]).nodes).toEqual([]);
    const only = layoutEntityGraph([node("9001", "tw_company", 1, true)]);
    expect(only.nodes).toHaveLength(1);
    expect(only.ringRadii).toEqual([]);
  });

  it("lays out a second node flagged focus as a peer, never on top of the first", () => {
    const layout = layoutEntityGraph([
      node("9001", "tw_company", 1, true),
      node("9002", "tw_company", 1, true),
    ]);
    expect(layout.nodes.filter((p) => p.ring === 0)).toHaveLength(1);
    expect(layout.nodes.filter((p) => p.ring === 2)).toHaveLength(1);
  });
});

describe("linkEnds", () => {
  it("runs from rim to rim", () => {
    const layout = layoutEntityGraph(sample());
    const a = layout.nodes.find((p) => p.ring === 0);
    const b = layout.nodes.find((p) => p.node.id === "e-laser");
    if (!a || !b) throw new Error("fixture");
    const { x1, y1, x2, y2 } = linkEnds(a, b, 0);
    expect(Math.hypot(x1 - a.x, y1 - a.y)).toBeCloseTo(a.r, 6);
    expect(Math.hypot(x2 - b.x, y2 - b.y)).toBeCloseTo(b.r, 6);
  });
});

describe("neighbourIds", () => {
  const links: EntityLink[] = [
    { source: "a", target: "b", weight: 1, kind: "mention" },
    { source: "c", target: "a", weight: 1, kind: "supply" },
    { source: "d", target: "e", weight: 1, kind: "cooccurrence" },
  ];
  it("returns both directions and excludes unrelated nodes", () => {
    expect([...neighbourIds(links, "a")].sort()).toEqual(["b", "c"]);
    expect(neighbourIds(links, "z").size).toBe(0);
  });
});
