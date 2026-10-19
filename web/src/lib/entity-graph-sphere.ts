/**
 * Deterministic sphere layout for the 關聯圖譜's 3D view. Pure functions only: the same nodes
 * always land on the same points (no randomness, no per-frame physics), so the sphere is stable
 * between renders, filter changes and reloads.
 *
 * The company in focus sits at the front-centre of a unit sphere (towards the camera, +z). Every
 * other node takes a point of a Fibonacci sphere (an even spread with no clumps), handed out in
 * type order then weight order, heaviest first. The points run pole to pole, so each type fills
 * one band of latitude and the heaviest nodes of a type sit at the top of its band.
 */
import { compareNodes } from "@/lib/entity-graph-layout";
import type { EntityNode } from "@/types/entity-graph";

export type Vec3 = readonly [number, number, number];

export type SpherePlacedNode = {
  readonly node: EntityNode;
  /** Point on the unit sphere. */
  readonly position: Vec3;
  /** Drawn radius, in unit-sphere units; area-like in the node's weight. */
  readonly size: number;
};

export const SPHERE_FOCUS_SIZE = 0.17;
const MIN_NODE_SIZE = 0.065;
const MAX_NODE_SIZE = 0.135;
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const FRONT: Vec3 = [0, 0, 1];

/** Node size grows with the square root of its weight so area, not width, tracks mentions. */
export function sphereNodeSize(weight: number, maxWeight: number): number {
  if (!(maxWeight > 0) || !(weight > 0)) {
    return MIN_NODE_SIZE;
  }
  const share = Math.min(1, weight / maxWeight);
  return MIN_NODE_SIZE + (MAX_NODE_SIZE - MIN_NODE_SIZE) * Math.sqrt(share);
}

/** `count` evenly spread points on the unit sphere, ordered from the top pole to the bottom. */
export function fibonacciSphere(count: number): Vec3[] {
  return Array.from({ length: count }, (_, index) => {
    const y = 1 - (2 * (index + 0.5)) / count;
    const ring = Math.sqrt(Math.max(0, 1 - y * y));
    const angle = index * GOLDEN_ANGLE;
    return [Math.cos(angle) * ring, y, Math.sin(angle) * ring] as const;
  });
}

/** Turn about the vertical axis that brings the point's longitude to the front (x = 0, z > 0). */
function yawToFront([x, , z]: Vec3): number {
  return Math.atan2(-x, z) + 0; // `+ 0` turns -0 into 0
}

function turnAboutY([x, y, z]: Vec3, yaw: number): Vec3 {
  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  return [x * cos + z * sin, y, -x * sin + z * cos];
}

export function layoutEntitySphere(nodes: readonly EntityNode[]): SpherePlacedNode[] {
  const sorted = [...nodes].sort(compareNodes);
  const maxWeight = sorted.reduce(
    (max, node) => (node.focus ? max : Math.max(max, node.weight)),
    0,
  );
  const focusNode = sorted
    .filter((node) => node.focus)
    .sort((a, b) => a.id.localeCompare(b.id))
    .at(0);
  const others = sorted.filter((node) => node !== focusNode);

  // One slot is reserved for the focus: the point nearest the equator, turned to the front. The
  // focus is then drawn at the exact front-centre and its slot stays empty.
  const slotCount = others.length + (focusNode ? 1 : 0);
  const points = fibonacciSphere(slotCount);
  const focusSlot = focusNode ? Math.round((slotCount - 1) / 2) : -1;
  const reserved = points[focusSlot];
  const yaw = reserved ? yawToFront(reserved) : 0;
  const free = points.filter((_, index) => index !== focusSlot).map((p) => turnAboutY(p, yaw));

  const placed: SpherePlacedNode[] = [];
  if (focusNode) {
    placed.push({ node: focusNode, position: FRONT, size: SPHERE_FOCUS_SIZE });
  }
  others.forEach((node, index) => {
    const position = free[index];
    if (position) {
      placed.push({ node, position, size: sphereNodeSize(node.weight, maxWeight) });
    }
  });
  return placed;
}

export type FrontRotation = { readonly yaw: number; readonly pitch: number };

/**
 * The turn that brings `position` to the front-centre: first about the vertical axis (`yaw`),
 * then about the horizontal one (`pitch`). Applied as Euler (pitch, yaw, 0) in x-y-z order, so the
 * sphere never rolls and "up" stays up however many nodes are brought to the front in a row.
 */
export function frontRotation(position: Vec3): FrontRotation {
  const yaw = yawToFront(position);
  const [, y, z] = turnAboutY(position, yaw);
  return { yaw, pitch: Math.atan2(y, z) };
}

/** `target` shifted by whole turns to the copy nearest `current`, so a turn takes the short way. */
export function nearestAngle(current: number, target: number): number {
  const turn = 2 * Math.PI;
  return target - turn * Math.round((target - current) / turn);
}

const FADE_SPAN = 0.45;

/** Label opacity by depth `z` (1 = front of the sphere): none on the back half, fading in across
 * the front. `minZ` is the depth a label starts to appear at (raise it where there is little
 * room, so fewer labels compete). */
export function labelOpacity(z: number, minZ = 0.15): number {
  return Math.min(1, Math.max(0, (z - minZ) / FADE_SPAN));
}

/** Node opacity by depth: nodes on the far side are dimmed, never removed. */
export function nodeOpacity(z: number): number {
  const share = Math.min(1, Math.max(0, (z + 0.25) / 0.6));
  return 0.28 + 0.72 * share;
}

export type LinkEnds = { readonly start: Vec3; readonly end: Vec3; readonly direction: Vec3 };

/** A line between two nodes' edges (not their centres), so an arrowhead lands on the target's
 * rim. `direction` is the unit vector from `from` to `to`. */
export function sphereLinkEnds(
  from: SpherePlacedNode,
  to: SpherePlacedNode,
  pad = 0.012,
): LinkEnds {
  const [fx, fy, fz] = from.position;
  const [tx, ty, tz] = to.position;
  const dx = tx - fx;
  const dy = ty - fy;
  const dz = tz - fz;
  const distance = Math.hypot(dx, dy, dz);
  if (distance === 0) {
    return { start: from.position, end: to.position, direction: [0, 0, 0] };
  }
  const direction: Vec3 = [dx / distance, dy / distance, dz / distance];
  const startGap = from.size + pad;
  const endGap = to.size + pad;
  if (distance <= startGap + endGap) {
    return { start: from.position, end: to.position, direction };
  }
  return {
    start: [
      fx + direction[0] * startGap,
      fy + direction[1] * startGap,
      fz + direction[2] * startGap,
    ],
    end: [tx - direction[0] * endGap, ty - direction[1] * endGap, tz - direction[2] * endGap],
    direction,
  };
}
