"use client";

import { useFrame } from "@react-three/fiber";
import { type ReactNode, useMemo, useRef } from "react";
import * as THREE from "three";
import { drawIconOnCanvas, type SectorIconKey } from "@/lib/sector-rotation-icons";

const BADGE_TEXTURE_SIZE = 128;
const MIN_BADGE_SCALE = 0.62;
const MAX_BADGE_SCALE = 1.05;
const HOVER_SCALE_MULTIPLIER = 1.28;

/** Draws one badge's full texture (colored glow disc + solid circle + icon on top) into an
 * offscreen `<canvas>`, once per (color, icon, weight) combination — the `CanvasTexture`
 * `SectorBadge`'s sprite material samples. Baking the icon into the SAME texture as the colored
 * circle (rather than layering a second sprite) is what keeps this to one draw call per badge and
 * avoids z-fighting between two coplanar sprites. */
function paintBadgeTexture(icon: SectorIconKey, color: string): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = BADGE_TEXTURE_SIZE;
  canvas.height = BADGE_TEXTURE_SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;
  const cx = BADGE_TEXTURE_SIZE / 2;
  const cy = BADGE_TEXTURE_SIZE / 2;

  // Soft additive-style glow — a radial gradient fading to transparent, painted first so the
  // solid disc + icon sit crisply on top of it.
  const glowRadius = BADGE_TEXTURE_SIZE * 0.48;
  const gradient = ctx.createRadialGradient(cx, cy, 0, cx, cy, glowRadius);
  gradient.addColorStop(0, `${color}aa`);
  gradient.addColorStop(0.5, `${color}44`);
  gradient.addColorStop(1, `${color}00`);
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, BADGE_TEXTURE_SIZE, BADGE_TEXTURE_SIZE);

  // Solid colored disc — the badge's own "pin" body, like a map marker.
  const discRadius = BADGE_TEXTURE_SIZE * 0.28;
  ctx.beginPath();
  ctx.arc(cx, cy, discRadius, 0, Math.PI * 2);
  ctx.fillStyle = color;
  ctx.fill();

  // Dark icon on top of the disc (governance's own "graphic + color double-encoding" —
  // web/OVERVIEW.md's colorblind-safety note in `sector-rotation-icons.tsx`'s own docstring),
  // painted at a fixed dark tone so it reads against every quadrant color.
  drawIconOnCanvas(ctx, icon, cx, cy, discRadius * 1.35, "#05070b");

  return canvas;
}

const textureCache = new Map<string, THREE.CanvasTexture>();

function textureFor(icon: SectorIconKey, color: string): THREE.CanvasTexture {
  const key = `${icon}:${color}`;
  const cached = textureCache.get(key);
  if (cached) return cached;
  const canvas = paintBadgeTexture(icon, color);
  const texture = new THREE.CanvasTexture(canvas);
  texture.needsUpdate = true;
  textureCache.set(key, texture);
  return texture;
}

/**
 * One sector's billboarded icon-pin sprite, colored by its CURRENT quadrant (recomputed every
 * frame from its live-interpolated RS-Ratio/RS-Momentum — a sector's badge color changes the
 * instant it crosses an axis during playback, exactly like the reference design's quadrant-color
 * icon binding). `alphaTest` (not blending) avoids the sorting flicker a transparent circle
 * sprite gets at overlapping depths — the reference spec's own explicit ask.
 *
 * `hovered`/`selected` drive the ~1.25-1.3x scale-up + brighter glow the `RankPanel` row-hover
 * delegation expects; `weight` (0-1, this sector's share of latest turnover) sets the badge's
 * baseline size independent of hover state.
 */
export function SectorBadge({
  position,
  icon,
  color,
  weight,
  hovered,
  introProgress,
  onHoverChange,
  onSelect,
}: {
  readonly position: readonly [number, number, number];
  readonly icon: SectorIconKey;
  readonly color: string;
  readonly weight: number;
  readonly hovered: boolean;
  readonly introProgress: number;
  readonly onHoverChange: (hovered: boolean) => void;
  readonly onSelect: () => void;
}): ReactNode {
  const spriteRef = useRef<THREE.Sprite>(null);
  const texture = useMemo(() => textureFor(icon, color), [icon, color]);
  const baseScale =
    MIN_BADGE_SCALE + Math.min(Math.max(weight, 0), 1) * (MAX_BADGE_SCALE - MIN_BADGE_SCALE);

  useFrame(() => {
    if (!spriteRef.current) return;
    const targetScale = (hovered ? baseScale * HOVER_SCALE_MULTIPLIER : baseScale) * introProgress;
    const current = spriteRef.current.scale.x;
    const eased = current + (targetScale - current) * 0.2;
    spriteRef.current.scale.setScalar(eased);
  });

  return (
    // biome-ignore lint/a11y/noStaticElementInteractions: a three.js sprite, not DOM; chips give the keyboard path
    <sprite
      ref={spriteRef}
      position={position}
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
      <spriteMaterial
        map={texture}
        transparent
        alphaTest={0.08}
        depthWrite={false}
        opacity={introProgress}
      />
    </sprite>
  );
}
