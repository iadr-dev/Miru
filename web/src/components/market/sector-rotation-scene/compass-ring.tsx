"use client";

import { useFrame } from "@react-three/fiber";
import { type ReactNode, useMemo, useRef } from "react";
import * as THREE from "three";
import { QUADRANT_COLORS, RRG_PLANE_HALF_EXTENT } from "./rrg-layout";

const RING_RADIUS = RRG_PLANE_HALF_EXTENT * 1.35;
const RING_SEGMENTS = 128;

/** Builds one quarter-arc (90°) of the ring as a `THREE.Vector3[]`, offset so quadrant N's arc
 * starts at `N * 90°` — used to build each of the 4 colored arcs making up the ring's own
 * four-color gradient-by-quadrant look (a real gradient shader would be overkill for 4 flat
 * quadrant colors; 4 solid arcs read the same at this radius). */
function arcPoints(startDeg: number, endDeg: number, radius: number): THREE.Vector3[] {
  const points: THREE.Vector3[] = [];
  const segments = Math.round(RING_SEGMENTS / 4);
  for (let i = 0; i <= segments; i++) {
    const deg = startDeg + ((endDeg - startDeg) * i) / segments;
    const rad = (deg * Math.PI) / 180;
    points.push(new THREE.Vector3(Math.cos(rad) * radius, 0, Math.sin(rad) * radius));
  }
  return points;
}

const QUADRANT_ARCS: readonly [number, number, string][] = [
  [0, 90, QUADRANT_COLORS.leading],
  [90, 180, QUADRANT_COLORS.improving],
  [180, 270, QUADRANT_COLORS.lagging],
  [270, 360, QUADRANT_COLORS.weakening],
];

/**
 * The outer four-color compass ring framing the quadrant floor, with a needle that rotates to
 * reflect overall playback progress (`playbackFraction`, 0-1 across the plotted week range) —
 * purely a decorative "where are we in the cycle" indicator, not itself plotting any sector's
 * data (matches the reference screenshot's own ring+needle motif).
 */
export function CompassRing({
  introProgress,
  playbackFraction,
  reducedMotion,
}: {
  readonly introProgress: number;
  readonly playbackFraction: number;
  readonly reducedMotion: boolean;
}): ReactNode {
  const arcs = useMemo(
    () =>
      QUADRANT_ARCS.map(([start, end, color]) => ({
        points: arcPoints(start, end, RING_RADIUS),
        color,
      })),
    [],
  );
  const needleRef = useRef<THREE.Group>(null);

  useFrame(() => {
    if (!needleRef.current) return;
    const targetAngle = playbackFraction * Math.PI * 2;
    if (reducedMotion) {
      needleRef.current.rotation.y = -targetAngle;
      return;
    }
    // Critically-damped ease toward the target angle rather than snapping — smooths out the
    // sub-frame jitter from `currentWeek`'s own continuous advance.
    const current = needleRef.current.rotation.y;
    const target = -targetAngle;
    needleRef.current.rotation.y = current + (target - current) * 0.12;
  });

  return (
    <group>
      {arcs.map(({ points, color }) => (
        <line key={color}>
          <bufferGeometry
            onUpdate={(geometry) => geometry.setFromPoints(points)}
            attach="geometry"
          />
          <lineBasicMaterial color={color} transparent opacity={0.55 * introProgress} />
        </line>
      ))}
      <group ref={needleRef}>
        <line>
          <bufferGeometry
            onUpdate={(geometry) =>
              geometry.setFromPoints([
                new THREE.Vector3(0, 0.02, 0),
                new THREE.Vector3(RING_RADIUS * 0.92, 0.02, 0),
              ])
            }
            attach="geometry"
          />
          <lineBasicMaterial color="#e6eaf0" transparent opacity={0.7 * introProgress} />
        </line>
      </group>
    </group>
  );
}
