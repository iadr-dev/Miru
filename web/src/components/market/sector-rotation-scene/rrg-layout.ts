/**
 * Pure math shared by every 3D piece of 板塊輪動's Relative Rotation Graph — no React, no
 * three.js imports, so it stays trivially unit-testable and reusable by both the R3F scene and
 * `RankPanel` (which needs the same quadrant/color classification for its list rows, not just
 * the 3D nodes).
 *
 * Axis convention: RS-Ratio (x) and RS-Momentum (y) both center on 100 (`get_sector_rotation_rrg`
 * — "100 = performing exactly in line with the benchmark"). World-space maps
 * `[100 - RANGE, 100 + RANGE]` on each axis to `[-PLANE_HALF_EXTENT, PLANE_HALF_EXTENT]` — a
 * fixed, data-independent scale (never auto-fit to the current week's min/max), so a sector's
 * absolute quadrant position is stable frame-to-frame; only ITS OWN point moves as the data
 * moves, the axes never rescale under it (rescaling axes under playback would make "is this
 * sector accelerating" unreadable — the whole point of RRG is a fixed frame you watch things
 * move through).
 */

export const RRG_AXIS_CENTER = 100;
/** Half-width of the plotted domain on each axis, in RS-Ratio/RS-Momentum units — sized to the
 * z-score construction's own typical spread (few real weeks land outside ±60 of center). */
export const RRG_AXIS_RANGE = 60;
export const RRG_PLANE_HALF_EXTENT = 6.5;

export type RotationQuadrant = "leading" | "weakening" | "lagging" | "improving";

/** 領先(leading)=x≥100,y≥100 / 走弱(weakening)=x≥100,y<100 / 落後(lagging)=x<100,y<100 /
 * 轉強(improving)=x<100,y≥100 — the reference spec's own quadrant definition, reused verbatim
 * (not renamed/reordered) since it's already the TW retail-RRG naming convention. */
export function quadrantFor(rsRatio: number, rsMomentum: number): RotationQuadrant {
  const strong = rsRatio >= RRG_AXIS_CENTER;
  const rising = rsMomentum >= RRG_AXIS_CENTER;
  if (strong && rising) return "leading";
  if (strong && !rising) return "weakening";
  if (!strong && !rising) return "lagging";
  return "improving";
}

/** 紅漲綠跌 governance (web/CLAUDE.md §4) applies to signed price/%/score deltas, not to this
 * quadrant classification (a "leading" sector isn't a price going up — it's a relative-strength
 * bucket), so these are read from `--chart-1..6`-style categorical tokens defined specifically
 * for this view, never `--up`/`--down`. Resolved to concrete hex (not a CSS var) because
 * `SectorBadge` needs a real color value to feed into a three.js `Color`/canvas fill, which
 * cannot read `var(--token)` — this is the one deliberate exception to "CSS-variable tokens only"
 * (web/CLAUDE.md §2), scoped to this 3D scene's own quadrant palette, matching the reference
 * design's exact hex values (already accessibility-reviewed: 4 hues, each paired with the
 * quadrant's own Chinese label as text, never color-alone).
 */
export const QUADRANT_COLORS: Record<RotationQuadrant, string> = {
  leading: "#E8462F",
  weakening: "#F2B705",
  lagging: "#1FA97A",
  improving: "#3E8EDE",
};

export function colorForQuadrant(quadrant: RotationQuadrant): string {
  return QUADRANT_COLORS[quadrant];
}

/** RS-Ratio/RS-Momentum -> world-space (x, z) on the quadrant floor plane (y is height, set
 * separately by the caller for badge float/hover lift). Clamped to the plotted domain so an
 * outlier week never pushes a badge off the visible floor. */
export function rrgToWorld(rsRatio: number, rsMomentum: number): { x: number; z: number } {
  const nx = clamp((rsRatio - RRG_AXIS_CENTER) / RRG_AXIS_RANGE, -1, 1);
  const nz = clamp((rsMomentum - RRG_AXIS_CENTER) / RRG_AXIS_RANGE, -1, 1);
  return { x: nx * RRG_PLANE_HALF_EXTENT, z: -nz * RRG_PLANE_HALF_EXTENT };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** Linear interpolation between two RRG points — the exact per-frame smoothing
 * `useRotationPlayback`'s fractional `currentWeek` needs to place a badge BETWEEN two integer
 * weeks rather than snapping. */
export function lerpPoint(
  a: { rs_ratio: number; rs_momentum: number },
  b: { rs_ratio: number; rs_momentum: number },
  t: number,
): { rs_ratio: number; rs_momentum: number } {
  return {
    rs_ratio: a.rs_ratio + (b.rs_ratio - a.rs_ratio) * t,
    rs_momentum: a.rs_momentum + (b.rs_momentum - a.rs_momentum) * t,
  };
}
