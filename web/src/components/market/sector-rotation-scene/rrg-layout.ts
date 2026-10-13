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

/** 紅漲綠跌 governance (web/OVERVIEW.md §4) applies to signed price/%/score deltas, not to this
 * quadrant classification (a "leading" sector isn't a price going up — it's a relative-strength
 * bucket), so these are read from `--chart-1..6`-style categorical tokens defined specifically
 * for this view, never `--up`/`--down`. Resolved to concrete hex (not a CSS var) because
 * `SectorBadge` needs a real color value to feed into a three.js `Color`/canvas fill, which
 * cannot read `var(--token)` — this is the one deliberate exception to "CSS-variable tokens only"
 * (web/OVERVIEW.md §2), scoped to this 3D scene's own quadrant palette, matching the reference
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

// ---------------------------------------------------------------------------
// Flat-view helpers: names, label placement, and quadrant transitions
// ---------------------------------------------------------------------------

/** The quadrants in the order the legend and counts list them (and the order a sector normally
 * travels them, clockwise: 轉強 -> 領先 -> 走弱 -> 落後). */
export const QUADRANT_ORDER: readonly RotationQuadrant[] = [
  "leading",
  "weakening",
  "lagging",
  "improving",
];

/** A sector's name without TWSE's "類指數"/"指數" suffix, for chips and in-scene labels where the
 * full index name (電腦及週邊設備類指數) would not fit. Falls back to the full name if stripping
 * would leave nothing. */
export function shortSectorName(name: string): string {
  const trimmed = name.trim();
  const short = trimmed.replace(/類?指數$/, "");
  return short.length > 0 ? short : trimmed;
}

export interface LabelHead {
  readonly id: string;
  readonly name: string;
  readonly x: number;
  readonly z: number;
}

export interface LabelOffset {
  readonly dx: number;
  readonly dz: number;
}

// World-space size of one label, tuned to the flat view's default zoom: a CJK character at the
// label font size is about this wide, plus padding for the pill around it.
const LABEL_CHAR_WIDTH = 0.34;
const LABEL_PADDING = 0.5;
const LABEL_HEIGHT_UNITS = 0.46;
// Tried in order: just above the head (screen-up is -z), then below, then further out, then
// shifted sideways - the first spot that overlaps no label already placed wins.
const LABEL_CANDIDATES: readonly LabelOffset[] = [
  { dx: 0, dz: -0.62 },
  { dx: 0, dz: 0.62 },
  { dx: 0, dz: -1.14 },
  { dx: 0, dz: 1.14 },
  { dx: 1.1, dz: -0.62 },
  { dx: -1.1, dz: -0.62 },
  { dx: 1.1, dz: 0.62 },
  { dx: -1.1, dz: 0.62 },
];

interface Box {
  readonly left: number;
  readonly right: number;
  readonly top: number;
  readonly bottom: number;
}

function boxFor(head: LabelHead, offset: LabelOffset): Box {
  const halfWidth = ([...head.name].length * LABEL_CHAR_WIDTH + LABEL_PADDING) / 2;
  const centerX = head.x + offset.dx;
  const centerZ = head.z + offset.dz;
  return {
    left: centerX - halfWidth,
    right: centerX + halfWidth,
    top: centerZ - LABEL_HEIGHT_UNITS / 2,
    bottom: centerZ + LABEL_HEIGHT_UNITS / 2,
  };
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

/** Where to put each sector's name so the names do not pile on top of each other when sectors
 * cluster (they often do - the earlier hover-only labels were a response to exactly that smear).
 * Placement is greedy and deterministic: heads are handled in a stable order, each takes the first
 * candidate spot that overlaps no label already placed, and falls back to the default spot when
 * everything is taken. Works in world units, so it holds for both the flat and the tilted view. */
export function layoutLabels(heads: readonly LabelHead[]): Map<string, LabelOffset> {
  const placed: Box[] = [];
  const offsets = new Map<string, LabelOffset>();
  const ordered = [...heads].sort((a, b) => a.z - b.z || a.x - b.x || a.id.localeCompare(b.id));
  for (const head of ordered) {
    const fallback = LABEL_CANDIDATES[0] as LabelOffset;
    const chosen =
      LABEL_CANDIDATES.find((offset) => {
        const box = boxFor(head, offset);
        return !placed.some((other) => overlaps(box, other));
      }) ?? fallback;
    placed.push(boxFor(head, chosen));
    offsets.set(head.id, chosen);
  }
  return offsets;
}

export interface QuadrantTransition {
  readonly industryId: string;
  readonly industryName: string;
  readonly from: RotationQuadrant;
  readonly to: RotationQuadrant;
}

interface WeeklySeries {
  readonly industry_id: string;
  readonly industry_name: string;
  readonly points: readonly { readonly rs_ratio: number; readonly rs_momentum: number }[];
}

/** Which sectors changed quadrant between two whole weeks. Compares the weeks themselves, never the
 * smoothed in-between positions, so a sector hovering on an axis cannot flicker notices. */
export function quadrantTransitions(
  sectors: readonly WeeklySeries[],
  fromWeek: number,
  toWeek: number,
): QuadrantTransition[] {
  const transitions: QuadrantTransition[] = [];
  for (const sector of sectors) {
    const before = sector.points[fromWeek];
    const after = sector.points[toWeek];
    if (!before || !after) continue;
    const from = quadrantFor(before.rs_ratio, before.rs_momentum);
    const to = quadrantFor(after.rs_ratio, after.rs_momentum);
    if (from !== to) {
      transitions.push({
        industryId: sector.industry_id,
        industryName: sector.industry_name,
        from,
        to,
      });
    }
  }
  return transitions;
}

/** How many sectors sit in each quadrant at a whole week. */
export function quadrantCounts(
  sectors: readonly WeeklySeries[],
  week: number,
): Record<RotationQuadrant, number> {
  const counts: Record<RotationQuadrant, number> = {
    leading: 0,
    weakening: 0,
    lagging: 0,
    improving: 0,
  };
  for (const sector of sectors) {
    const point = sector.points[Math.min(Math.max(week, 0), sector.points.length - 1)];
    if (point) counts[quadrantFor(point.rs_ratio, point.rs_momentum)] += 1;
  }
  return counts;
}
