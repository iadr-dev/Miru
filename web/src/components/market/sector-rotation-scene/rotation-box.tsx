"use client";

import { Html } from "@react-three/drei";
import { type ReactNode, useMemo } from "react";
import * as THREE from "three";
import {
  colorForQuadrant,
  PLANE_HALF_EXTENT,
  QUADRANT_ORDER,
  type RotationQuadrant,
} from "./rrg-layout";
import { DEPTH_PER_WEEK } from "./sector-trail";

const FRONT_OPACITY = 0.1;
const BACK_OPACITY = 0.025;
const CAPTION_X = 7;
const CAPTION_Y = 8;

// Which side of the axes each quadrant sits on: [x sign, y sign].
const QUADRANT_SIGNS: Record<RotationQuadrant, readonly [number, number]> = {
  leading: [1, 1],
  weakening: [1, -1],
  lagging: [-1, -1],
  improving: [-1, 1],
};

/** The frame the sectors move in: four tinted quadrant sheets on the plane, faint twins at the
 * far end of the time axis, a wireframe box between them, the three axes, and each quadrant's
 * name written into its corner. Static - it never moves with the data. */
export function RotationBox({
  weekCount,
  quadrantLabels,
}: {
  readonly weekCount: number;
  readonly quadrantLabels: Record<RotationQuadrant, string>;
}): ReactNode {
  const depth = Math.max(weekCount - 1, 1) * DEPTH_PER_WEEK;
  const size = PLANE_HALF_EXTENT * 2;

  const frame = useMemo(
    () => new THREE.EdgesGeometry(new THREE.BoxGeometry(size, size, depth)),
    [size, depth],
  );
  const axes = useMemo(() => {
    const h = PLANE_HALF_EXTENT;
    return new THREE.BufferGeometry().setFromPoints(
      [
        [-h, 0, 0],
        [h, 0, 0],
        [0, -h, 0],
        [0, h, 0],
        [0, 0, 0],
        [0, 0, -depth],
      ].map(([x = 0, y = 0, z = 0]) => new THREE.Vector3(x, y, z)),
    );
  }, [depth]);

  return (
    <>
      {QUADRANT_ORDER.map((quadrant) => {
        const [sx, sy] = QUADRANT_SIGNS[quadrant];
        const color = colorForQuadrant(quadrant);
        const half = PLANE_HALF_EXTENT / 2;
        return (
          <group key={quadrant}>
            <mesh position={[sx * half, sy * half, 0]}>
              <planeGeometry args={[PLANE_HALF_EXTENT, PLANE_HALF_EXTENT]} />
              <meshBasicMaterial
                color={color}
                transparent
                opacity={FRONT_OPACITY}
                side={THREE.DoubleSide}
                depthWrite={false}
              />
            </mesh>
            <mesh position={[sx * half, sy * half, -depth]}>
              <planeGeometry args={[PLANE_HALF_EXTENT, PLANE_HALF_EXTENT]} />
              <meshBasicMaterial
                color={color}
                transparent
                opacity={BACK_OPACITY}
                side={THREE.DoubleSide}
                depthWrite={false}
              />
            </mesh>
            <Html position={[sx * CAPTION_X, sy * CAPTION_Y, 0]} center zIndexRange={[5, 0]}>
              <span
                className="pointer-events-none whitespace-nowrap font-semibold text-[12px] opacity-80"
                style={{ color }}
              >
                {quadrantLabels[quadrant]}
              </span>
            </Html>
          </group>
        );
      })}
      <lineSegments geometry={frame} position={[0, 0, -depth / 2]}>
        <lineBasicMaterial color="#5a6395" transparent opacity={0.35} />
      </lineSegments>
      <lineSegments geometry={axes}>
        <lineBasicMaterial color="#ffffff" transparent opacity={0.45} />
      </lineSegments>
    </>
  );
}
