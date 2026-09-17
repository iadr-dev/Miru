"use client";

import { type ReactNode, useMemo } from "react";
import * as THREE from "three";
import { QUADRANT_COLORS, RRG_PLANE_HALF_EXTENT } from "./rrg-layout";

/** Concentric rings drawn as `Line` geometry — not a texture, so they stay perfectly crisp at
 * any zoom. Ring radii are a fixed fraction of the plane's half-extent (independent of any data
 * value — a decorative reference grid, same non-trading-motion spirit as `QUADRANT_COLORS`'s own
 * fixed hex palette never being data-driven). */
const RING_FRACTIONS = [0.25, 0.5, 0.75, 1];

function ringPoints(radius: number, segments = 64): THREE.Vector3[] {
  const points: THREE.Vector3[] = [];
  for (let i = 0; i <= segments; i++) {
    const theta = (i / segments) * Math.PI * 2;
    points.push(new THREE.Vector3(Math.cos(theta) * radius, 0, Math.sin(theta) * radius));
  }
  return points;
}

/**
 * The flat plane the whole RRG plots onto: four half-transparent quadrant tiles (colored by
 * `QUADRANT_COLORS`, opacity animated by `introProgress` on mount per the spec's open-animation),
 * a concentric-ring reference grid, and the two crossing axis lines at RS-Ratio=100/
 * RS-Momentum=100. Purely geometric — no data plotted here (`SectorBadge`/`CometTail` plot the
 * actual sectors on top of this).
 */
export function QuadrantFloor({ introProgress }: { readonly introProgress: number }): ReactNode {
  const half = RRG_PLANE_HALF_EXTENT;
  const rings = useMemo(
    () => RING_FRACTIONS.map((f) => ({ fraction: f, points: ringPoints(half * f) })),
    [],
  );
  const opacity = 0.16 * introProgress;

  return (
    <group>
      {/* Four quadrant tiles — 領先(+x+z) 走弱(+x-z) 落後(-x-z) 轉強(-x+z). z is negated in
       * `rrgToWorld` (RS-Momentum grows toward -z), so the tile offsets below mirror that. */}
      <mesh position={[half / 2, 0, -half / 2]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[half, half]} />
        <meshBasicMaterial
          color={QUADRANT_COLORS.leading}
          transparent
          opacity={opacity}
          depthWrite={false}
        />
      </mesh>
      <mesh position={[half / 2, 0, half / 2]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[half, half]} />
        <meshBasicMaterial
          color={QUADRANT_COLORS.weakening}
          transparent
          opacity={opacity}
          depthWrite={false}
        />
      </mesh>
      <mesh position={[-half / 2, 0, half / 2]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[half, half]} />
        <meshBasicMaterial
          color={QUADRANT_COLORS.lagging}
          transparent
          opacity={opacity}
          depthWrite={false}
        />
      </mesh>
      <mesh position={[-half / 2, 0, -half / 2]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[half, half]} />
        <meshBasicMaterial
          color={QUADRANT_COLORS.improving}
          transparent
          opacity={opacity}
          depthWrite={false}
        />
      </mesh>

      {rings.map(({ fraction, points }) => (
        <line key={fraction}>
          <bufferGeometry
            onUpdate={(geometry) => geometry.setFromPoints(points)}
            attach="geometry"
          />
          <lineBasicMaterial
            color="#8a93a3"
            transparent
            opacity={0.18 * introProgress}
            depthWrite={false}
          />
        </line>
      ))}

      {/* Cross axes at RS-Ratio=100 / RS-Momentum=100. */}
      <line>
        <bufferGeometry
          onUpdate={(geometry) =>
            geometry.setFromPoints([new THREE.Vector3(-half, 0, 0), new THREE.Vector3(half, 0, 0)])
          }
          attach="geometry"
        />
        <lineBasicMaterial color="#8a93a3" transparent opacity={0.3 * introProgress} />
      </line>
      <line>
        <bufferGeometry
          onUpdate={(geometry) =>
            geometry.setFromPoints([new THREE.Vector3(0, 0, -half), new THREE.Vector3(0, 0, half)])
          }
          attach="geometry"
        />
        <lineBasicMaterial color="#8a93a3" transparent opacity={0.3 * introProgress} />
      </line>
    </group>
  );
}
