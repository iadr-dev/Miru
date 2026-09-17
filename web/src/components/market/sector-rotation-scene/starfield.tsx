"use client";

import { useFrame } from "@react-three/fiber";
import { type ReactNode, useMemo, useRef } from "react";
import type * as THREE from "three";

const STAR_COUNT = 260;
const FIELD_RADIUS = 40;
/** Purely ambient, non-magnitude-scaled rotation speed — never tied to any sector's data
 * (web/CLAUDE.md §4 non-trading-motion), and deliberately independent of `introProgress`: the
 * spec calls for the starfield to "always exist" as the scene's own backdrop, unlike the
 * floor/ring/badges which fade in on mount. */
const ROTATION_RADIANS_PER_SECOND = 0.008;

function randomSpherePoints(count: number, radius: number, seed: number): Float32Array {
  // A tiny mulberry32 PRNG — deterministic per mount (no `Math.random()` dependency across
  // server/client, and stable across re-renders since `seed` is a module constant) so the
  // starfield doesn't visibly "pop" to a different arrangement on every fast-refresh.
  let state = seed;
  const next = (): number => {
    state = (state + 0x6d2b79f5) | 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const positions = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const r = radius * (0.6 + next() * 0.4);
    const theta = next() * Math.PI * 2;
    const phi = Math.acos(2 * next() - 1);
    positions[i * 3] = r * Math.sin(phi) * Math.cos(theta);
    positions[i * 3 + 1] = r * Math.sin(phi) * Math.sin(theta);
    positions[i * 3 + 2] = r * Math.cos(phi);
  }
  return positions;
}

/**
 * Sparse ambient background particles, slowly self-rotating — pure atmosphere, never plots data,
 * always present (not gated by `introProgress`) so the scene reads as "a console powering on
 * inside an existing starfield" rather than the stars themselves being part of the boot animation.
 */
export function Starfield({ reducedMotion }: { readonly reducedMotion: boolean }): ReactNode {
  const positions = useMemo(() => randomSpherePoints(STAR_COUNT, FIELD_RADIUS, 1337), []);
  const groupRef = useRef<THREE.Group>(null);

  useFrame((_state, delta) => {
    if (reducedMotion || !groupRef.current) return;
    groupRef.current.rotation.y += ROTATION_RADIANS_PER_SECOND * delta;
  });

  return (
    <group ref={groupRef}>
      <points>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" args={[positions, 3]} />
        </bufferGeometry>
        <pointsMaterial
          color="#4a5568"
          size={0.28}
          sizeAttenuation
          transparent
          opacity={0.55}
          depthWrite={false}
        />
      </points>
    </group>
  );
}
