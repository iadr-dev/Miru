"use client";

import { type ReactNode, useMemo } from "react";
import * as THREE from "three";
import { rrgToWorld } from "./rrg-layout";

const TAIL_WINDOW_WEEKS = 9;
const TAIL_HEIGHT = 0.03;

/**
 * One sector's sliding-window trail — the last `TAIL_WINDOW_WEEKS` weeks up to (and including)
 * `throughWeekIndex`, rendered as a `THREE.Line` whose per-vertex color fades from the
 * background tone at the tail's oldest point to the sector's own current quadrant color at its
 * newest (the badge's current position) — a literal comet tail, not a flat-colored trail.
 *
 * Recomputed only when `throughWeekIndex` changes (an integer week boundary), never every frame
 * — the reference spec's own "reduce recompute cost" note, since the underlying weekly point
 * list itself only changes at week boundaries; sub-week interpolation is the BADGE's job
 * (`SectorBadge`'s position prop), not the tail's.
 */
export function CometTail({
  points,
  throughWeekIndex,
  color,
  introProgress,
}: {
  readonly points: readonly { readonly rs_ratio: number; readonly rs_momentum: number }[];
  readonly throughWeekIndex: number;
  readonly color: string;
  readonly introProgress: number;
}): ReactNode {
  const { positions, colors } = useMemo(() => {
    const end = Math.min(throughWeekIndex, points.length - 1);
    const start = Math.max(0, end - TAIL_WINDOW_WEEKS + 1);
    const slice = points.slice(start, end + 1);
    if (slice.length < 2) return { positions: null, colors: null };

    const backgroundColor = new THREE.Color("#0b0e13");
    const headColor = new THREE.Color(color);
    const posArray = new Float32Array(slice.length * 3);
    const colorArray = new Float32Array(slice.length * 3);
    slice.forEach((point, index) => {
      const { x, z } = rrgToWorld(point.rs_ratio, point.rs_momentum);
      posArray[index * 3] = x;
      posArray[index * 3 + 1] = TAIL_HEIGHT;
      posArray[index * 3 + 2] = z;

      const t = slice.length === 1 ? 1 : index / (slice.length - 1);
      const blended = backgroundColor.clone().lerp(headColor, t);
      colorArray[index * 3] = blended.r;
      colorArray[index * 3 + 1] = blended.g;
      colorArray[index * 3 + 2] = blended.b;
    });
    return { positions: posArray, colors: colorArray };
  }, [points, throughWeekIndex, color]);

  if (!positions || !colors) return null;

  return (
    <line>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        <bufferAttribute attach="attributes-color" args={[colors, 3]} />
      </bufferGeometry>
      <lineBasicMaterial vertexColors transparent opacity={0.85 * introProgress} linewidth={1} />
    </line>
  );
}
