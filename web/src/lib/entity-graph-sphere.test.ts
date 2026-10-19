import { describe, expect, it } from "vitest";
import {
  fibonacciSphere,
  frontRotation,
  labelOpacity,
  layoutEntitySphere,
  nearestAngle,
  nodeOpacity,
  SPHERE_FOCUS_SIZE,
  sphereLinkEnds,
  sphereNodeSize,
  type Vec3,
} from "@/lib/entity-graph-sphere";
import type { EntityNode, EntityType } from "@/types/entity-graph";

function node(id: string, type: EntityType, weight: number, focus = false): EntityNode {
  const company = type === "tw_company" || type === "intl_company";
  return {
    id,
    label: id,
    type,
    symbol: company ? id : null,
    market: company ? "TW" : null,
    weight,
    focus,
  };
}

const TYPES: readonly EntityType[] = ["technology", "material", "application", "tw_company"];

function manyNodes(count: number): EntityNode[] {
  const rest = Array.from({ length: count }, (_, i) =>
    node(`n${String(i).padStart(2, "0")}`, TYPES[i % TYPES.length] ?? "technology", 1 + (i % 7)),
  );
  return [node("focus", "tw_company", 20, true), ...rest];
}

const length = (v: Vec3): number => Math.hypot(v[0], v[1], v[2]);
const distance = (a: Vec3, b: Vec3): number => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);

describe("fibonacciSphere", () => {
  it("returns unit vectors from the top pole down", () => {
    const points = fibonacciSphere(40);
    expect(points).toHaveLength(40);
    for (const point of points) expect(length(point)).toBeCloseTo(1, 10);
    const ys = points.map((p) => p[1]);
    expect(ys).toEqual([...ys].sort((a, b) => b - a));
  });
});

describe("layoutEntitySphere", () => {
  it("is deterministic and ignores input order", () => {
    const nodes = manyNodes(30);
    const a = layoutEntitySphere(nodes);
    const b = layoutEntitySphere([...nodes].reverse());
    const byId = (list: typeof a) => Object.fromEntries(list.map((p) => [p.node.id, p.position]));
    expect(byId(b)).toEqual(byId(a));
    expect(layoutEntitySphere(nodes)).toEqual(a);
  });

  it("puts the focus company at the front-centre and everything on the unit sphere", () => {
    const placed = layoutEntitySphere(manyNodes(30));
    const focus = placed.find((p) => p.node.focus);
    expect(focus?.position).toEqual([0, 0, 1]);
    expect(focus?.size).toBe(SPHERE_FOCUS_SIZE);
    expect(placed).toHaveLength(31);
    for (const p of placed) expect(length(p.position)).toBeCloseTo(1, 10);
  });

  it("keeps the other nodes apart from each other and from the focus", () => {
    const placed = layoutEntitySphere(manyNodes(60));
    for (const [i, a] of placed.entries()) {
      for (const b of placed.slice(i + 1)) {
        expect(distance(a.position, b.position)).toBeGreaterThan(0.2);
      }
    }
  });

  it("groups nodes by type into bands of latitude, heaviest first inside a band", () => {
    const placed = layoutEntitySphere(manyNodes(40)).filter((p) => !p.node.focus);
    const order = ["technology", "material", "application", "tw_company"];
    let lowestYSoFar = Number.POSITIVE_INFINITY;
    for (const type of order) {
      const band = placed.filter((p) => p.node.type === type);
      const ys = band.map((p) => p.position[1]);
      expect(Math.max(...ys)).toBeLessThanOrEqual(lowestYSoFar);
      lowestYSoFar = Math.min(...ys);
      const weights = [...band]
        .sort((a, b) => b.position[1] - a.position[1])
        .map((p) => p.node.weight);
      expect(weights).toEqual([...weights].sort((a, b) => b - a));
    }
  });

  it("sizes nodes by weight, never above the focus", () => {
    const placed = layoutEntitySphere(manyNodes(20));
    const heavy = placed.find((p) => p.node.weight === 7 && !p.node.focus);
    const light = placed.find((p) => p.node.weight === 1);
    expect(heavy?.size ?? 0).toBeGreaterThan(light?.size ?? 1);
    for (const p of placed) expect(p.size).toBeLessThanOrEqual(SPHERE_FOCUS_SIZE);
  });

  it("handles no nodes, a lone focus and a graph without a focus", () => {
    expect(layoutEntitySphere([])).toEqual([]);
    expect(layoutEntitySphere([node("a", "tw_company", 3, true)])).toHaveLength(1);
    const noFocus = layoutEntitySphere([node("a", "technology", 3), node("b", "material", 2)]);
    expect(noFocus).toHaveLength(2);
    for (const p of noFocus) expect(length(p.position)).toBeCloseTo(1, 10);
  });
});

describe("sphereNodeSize", () => {
  it("is area-like: sqrt of the share of the heaviest weight", () => {
    const min = sphereNodeSize(0, 10);
    const max = sphereNodeSize(10, 10);
    const quarter = sphereNodeSize(2.5, 10);
    expect(quarter - min).toBeCloseTo((max - min) / 2, 10);
    expect(sphereNodeSize(5, 0)).toBe(min);
  });
});

describe("frontRotation", () => {
  it("brings any point to (0, 0, 1) as Euler (pitch, yaw, 0)", () => {
    const placed = layoutEntitySphere(manyNodes(25));
    for (const { position } of placed) {
      const { yaw, pitch } = frontRotation(position);
      const [x, y, z] = position;
      // Ry(yaw) first, then Rx(pitch): the same order three.js uses for rotation (pitch, yaw, 0).
      const x1 = x * Math.cos(yaw) + z * Math.sin(yaw);
      const z1 = -x * Math.sin(yaw) + z * Math.cos(yaw);
      const y2 = y * Math.cos(pitch) - z1 * Math.sin(pitch);
      const z2 = y * Math.sin(pitch) + z1 * Math.cos(pitch);
      expect(x1).toBeCloseTo(0, 10);
      expect(y2).toBeCloseTo(0, 10);
      expect(z2).toBeCloseTo(1, 10);
    }
  });

  it("is the identity for the front", () => {
    expect(frontRotation([0, 0, 1])).toEqual({ yaw: 0, pitch: 0 });
  });
});

describe("nearestAngle", () => {
  it("takes the short way round", () => {
    expect(nearestAngle(0, 2 * Math.PI + 0.5)).toBeCloseTo(0.5, 10);
    expect(nearestAngle(3, -3)).toBeCloseTo(-3 + 2 * Math.PI, 10);
    expect(nearestAngle(1, 1.2)).toBeCloseTo(1.2, 10);
  });
});

describe("depth fades", () => {
  it("shows labels only on the front, fading in", () => {
    expect(labelOpacity(-1)).toBe(0);
    expect(labelOpacity(0)).toBe(0);
    expect(labelOpacity(0.15 + 0.225)).toBeCloseTo(0.5, 10);
    expect(labelOpacity(1)).toBe(1);
    expect(labelOpacity(0.5, 0.6)).toBe(0);
  });

  it("dims the far side without removing it", () => {
    expect(nodeOpacity(1)).toBe(1);
    expect(nodeOpacity(-1)).toBeGreaterThan(0.2);
    expect(nodeOpacity(-1)).toBeLessThan(nodeOpacity(0));
  });
});

describe("sphereLinkEnds", () => {
  const a = { node: node("a", "tw_company", 1), position: [0, 0, 1] as Vec3, size: 0.1 };
  const b = { node: node("b", "tw_company", 1), position: [1, 0, 1] as Vec3, size: 0.1 };

  it("stops each end at the node's rim and points from source to target", () => {
    const ends = sphereLinkEnds(a, b, 0);
    expect(ends.start).toEqual([0.1, 0, 1]);
    expect(ends.end).toEqual([0.9, 0, 1]);
    expect(ends.direction).toEqual([1, 0, 0]);
  });

  it("falls back to the centres when the nodes touch", () => {
    const near = { ...b, position: [0.15, 0, 1] as Vec3 };
    expect(sphereLinkEnds(a, near).start).toEqual(a.position);
  });
});
