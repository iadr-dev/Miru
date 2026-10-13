"use client";

import { type ReactNode, useLayoutEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import { type PlanePoint, planeAt, rrgToPlane } from "./rrg-layout";

/** World distance between two weeks on the time axis (which recedes behind the quadrant plane). */
export const DEPTH_PER_WEEK = 0.6;

/** How many recent weeks each trail shows, and how many curve samples sit between two weeks. */
export const TRAIL_WEEKS = 12;
const SAMPLES_PER_WEEK = 4;
/** Weeks of trail a sector shows at rest; hovering or selecting one shows the whole window. */
const RESTING_TRAIL_WEEKS = 5;
const TRAIL_VERTICES = (TRAIL_WEEKS - 1) * SAMPLES_PER_WEEK + 1;

const HEAD_RADIUS = 0.34;
const GLOW_SIZE = 2.6;
const SELECTED_SCALE = 1.7;
const HOVERED_SCALE = 1.35;
const DIMMED_HEAD_SCALE = 0.6;
const DIMMED_TRAIL = 0.18;
const DIMMED_GLOW = 0.25;
const GLOW_TEXTURE_SIZE = 64;

export type TrailEmphasis = "normal" | "hovered" | "selected" | "dimmed";

interface RrgPoint {
  readonly rs_ratio: number;
  readonly rs_momentum: number;
}

let glowTexture: THREE.CanvasTexture | null = null;

/** A soft white radial falloff, tinted per sector by the sprite's colour. Built once. */
function sharedGlowTexture(): THREE.CanvasTexture {
  if (glowTexture) return glowTexture;
  const canvas = document.createElement("canvas");
  canvas.width = GLOW_TEXTURE_SIZE;
  canvas.height = GLOW_TEXTURE_SIZE;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const mid = GLOW_TEXTURE_SIZE / 2;
    const gradient = ctx.createRadialGradient(mid, mid, 0, mid, mid, mid);
    gradient.addColorStop(0, "rgba(255,255,255,1)");
    gradient.addColorStop(0.3, "rgba(255,255,255,0.45)");
    gradient.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, GLOW_TEXTURE_SIZE, GLOW_TEXTURE_SIZE);
  }
  glowTexture = new THREE.CanvasTexture(canvas);
  return glowTexture;
}

/** A sector's weekly points on the quadrant plane. */
export function toPlane(points: readonly RrgPoint[], range: number): PlanePoint[] {
  return points.map((point) => rrgToPlane(point.rs_ratio, point.rs_momentum, range));
}

/** Where a sector's head sits at a fractional week, on the plane (z = 0). */
export function headPosition(
  points: readonly RrgPoint[],
  week: number,
  range: number,
): PlanePoint | null {
  return planeAt(toPlane(points, range), week);
}

/**
 * One sector in the time-box: a glowing head on the quadrant plane and a trail running back
 * through time behind it (one vertex per week, oldest furthest back and darkest, newest at the
 * head), drawn additively as a line plus dots so overlapping trails brighten instead of hiding
 * each other. The vertex buffers are rewritten whenever the playback week moves; nothing here
 * animates on its own, so reduced motion needs no special case.
 *
 * Emphasis is a visual weight only: a selected sector grows and keeps its full trail, everything
 * else dims, hovered gets a small lift. It never changes a position.
 */
export function SectorTrail({
  points,
  color,
  currentWeek,
  range,
  emphasis,
  onHoverChange,
  onSelect,
}: {
  readonly points: readonly RrgPoint[];
  readonly color: string;
  readonly currentWeek: number;
  readonly range: number;
  readonly emphasis: TrailEmphasis;
  readonly onHoverChange: (hovered: boolean) => void;
  readonly onSelect: () => void;
}): ReactNode {
  const headRef = useRef<THREE.Mesh>(null);
  const glowRef = useRef<THREE.Sprite>(null);

  const base = useMemo(() => new THREE.Color(color), [color]);
  const buffers = useMemo(() => {
    const positions = new Float32Array(TRAIL_VERTICES * 3);
    const colors = new Float32Array(TRAIL_VERTICES * 3);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const material = {
      vertexColors: true,
      transparent: true,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    } as const;
    return {
      positions,
      colors,
      geometry,
      line: new THREE.Line(geometry, new THREE.LineBasicMaterial(material)),
      dots: new THREE.Points(
        geometry,
        new THREE.PointsMaterial({ ...material, size: 3, sizeAttenuation: false }),
      ),
    };
  }, []);

  useLayoutEffect(() => {
    return () => {
      buffers.geometry.dispose();
      buffers.line.material.dispose();
      buffers.dots.material.dispose();
    };
  }, [buffers]);

  const dimmed = emphasis === "dimmed";

  useLayoutEffect(() => {
    const { positions, colors, geometry } = buffers;
    const weight = dimmed ? DIMMED_TRAIL : 1;
    const visibleWeeks =
      emphasis === "selected" || emphasis === "hovered" ? TRAIL_WEEKS - 1 : RESTING_TRAIL_WEEKS;
    const plane = toPlane(points, range);
    let firstDrawn = TRAIL_VERTICES;
    for (let k = TRAIL_VERTICES - 1; k >= 0; k--) {
      const age = (TRAIL_VERTICES - 1 - k) / SAMPLES_PER_WEEK;
      if (age > visibleWeeks) continue;
      const week = currentWeek - age;
      if (week < 0) break;
      const spot = planeAt(plane, week);
      if (!spot) break;
      const fade = (1 - age / (visibleWeeks + 1)) ** 1.5 * weight;
      positions.set([spot.x, spot.y, -age * DEPTH_PER_WEEK], k * 3);
      colors.set([base.r * fade, base.g * fade, base.b * fade], k * 3);
      firstDrawn = k;
    }
    geometry.setDrawRange(firstDrawn, TRAIL_VERTICES - firstDrawn);
    const attributes = geometry.attributes;
    if (attributes.position) attributes.position.needsUpdate = true;
    if (attributes.color) attributes.color.needsUpdate = true;
    // The trail's bounds move every frame; skip the stale culling box.
    buffers.line.frustumCulled = false;
    buffers.dots.frustumCulled = false;

    const head = headPosition(points, currentWeek, range);
    if (head && headRef.current && glowRef.current) {
      headRef.current.position.set(head.x, head.y, 0);
      glowRef.current.position.set(head.x, head.y, 0);
    }
  }, [buffers, points, currentWeek, range, base, dimmed, emphasis]);

  let headScale = 1;
  if (emphasis === "selected") headScale = SELECTED_SCALE;
  else if (emphasis === "hovered") headScale = HOVERED_SCALE;
  else if (dimmed) headScale = DIMMED_HEAD_SCALE;

  return (
    <>
      <primitive object={buffers.line} />
      <primitive object={buffers.dots} />
      {/* biome-ignore lint/a11y/noStaticElementInteractions: three.js mesh; chips are the keyboard path */}
      <mesh
        ref={headRef}
        scale={headScale}
        onPointerOver={(event) => {
          event.stopPropagation();
          onHoverChange(true);
        }}
        onPointerOut={() => onHoverChange(false)}
        onClick={(event) => {
          event.stopPropagation();
          onSelect();
        }}
      >
        <sphereGeometry args={[HEAD_RADIUS, 20, 16]} />
        <meshBasicMaterial color={color} />
      </mesh>
      <sprite ref={glowRef} scale={[GLOW_SIZE * headScale, GLOW_SIZE * headScale, 1]}>
        <spriteMaterial
          map={sharedGlowTexture()}
          color={color}
          blending={THREE.AdditiveBlending}
          depthWrite={false}
          transparent
          opacity={dimmed ? DIMMED_GLOW : 1}
        />
      </sprite>
    </>
  );
}
