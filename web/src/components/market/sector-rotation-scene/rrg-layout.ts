/**
 * Pure math shared by every 3D piece of 板塊輪動's Relative Rotation Graph — no React, no
 * three.js imports, so it stays trivially unit-testable and reusable by both the R3F scene and
 * `RankPanel` (which needs the same quadrant/color classification for its list rows, not just
 * the 3D nodes).
 *
 * Axis convention: RS-Ratio (x) and RS-Momentum (y) both center on 100 (`get_sector_rotation_rrg`
 * — "100 = performing exactly in line with the benchmark"). `rrgToPlane` maps the deviation from
 * 100 onto the quadrant plane with a scale fixed per dataset (`rrgRange`, never the current week's
 * min/max), so a sector's absolute quadrant position is stable frame-to-frame; only ITS OWN point moves as the data
 * moves, the axes never rescale under it (rescaling axes under playback would make "is this
 * sector accelerating" unreadable — the whole point of RRG is a fixed frame you watch things
 * move through).
 */

export const RRG_AXIS_CENTER = 100;

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

// ---------------------------------------------------------------------------
// Plane scale and per-sector colour
// ---------------------------------------------------------------------------

/** Half-width of the quadrant plane in world units; the plane spans [-10, 10] on both axes. */
export const PLANE_HALF_EXTENT = 10;
// Heads and trails stop just inside the frame, so an outlier never sits on (or past) the edge.
const PLANE_USABLE = 9.5;
const MIN_RANGE = 20;
const RANGE_STEP = 10;
const RANGE_PERCENTILE = 0.9;
// How hard the plane saturates: a deviation of exactly `range` lands at tanh(SATURATION) ~ 0.9 of
// the way to the edge, and anything larger bends in towards it rather than piling up on it.
const SATURATION = 1.5;

/** How far from 100 the plane reaches on each axis, in RS units. The backend's figures are
 * z-score style and swing far wider than a fixed +/-60 (live data runs from about -150 to +350 on
 * RS-Ratio), which pinned most sectors to the edge. This takes the 90th percentile of every
 * deviation across the WHOLE series, rounded up to a step - computed once per dataset, never per
 * frame, so the axes hold still while the sectors move through them. */
export function rrgRange(sectors: readonly WeeklySeries[]): number {
  const deviations: number[] = [];
  for (const sector of sectors) {
    for (const point of sector.points) {
      deviations.push(
        Math.abs(point.rs_ratio - RRG_AXIS_CENTER),
        Math.abs(point.rs_momentum - RRG_AXIS_CENTER),
      );
    }
  }
  if (deviations.length === 0) return MIN_RANGE;
  deviations.sort((a, b) => a - b);
  const index = Math.min(deviations.length - 1, Math.floor(deviations.length * RANGE_PERCENTILE));
  const value = deviations[index] ?? MIN_RANGE;
  return Math.max(MIN_RANGE, Math.ceil(value / RANGE_STEP) * RANGE_STEP);
}

/** RS-Ratio / RS-Momentum -> a position on the quadrant plane (x right, y up), centred on 100.
 * The curve is smooth and monotonic, so ordering and the 100 axes are exact; only the far tails
 * are compressed (the live figures are heavy-tailed, and a hard clamp pinned outliers to the edge). */
export function rrgToPlane(
  rsRatio: number,
  rsMomentum: number,
  range: number,
): { x: number; y: number } {
  return {
    x: Math.tanh(((rsRatio - RRG_AXIS_CENTER) / range) * SATURATION) * PLANE_USABLE,
    y: Math.tanh(((rsMomentum - RRG_AXIS_CENTER) / range) * SATURATION) * PLANE_USABLE,
  };
}

/** One hue per sector (heads, trails, labels, chips), so a sector is followed by colour while the
 * quadrant it sits in is read off the tinted plane and its caption. The 4 quadrant colours are
 * kept apart from these in spirit, not in a rule: a sector may share a hue with a quadrant. */
const SECTOR_PALETTE: readonly string[] = [
  "#60a5fa",
  "#a78bfa",
  "#fb923c",
  "#2dd4bf",
  "#f472b6",
  "#a3e635",
  "#facc15",
  "#22d3ee",
  "#f87171",
  "#c084fc",
  "#e879f9",
  "#4ade80",
];

export function sectorColor(index: number): string {
  return SECTOR_PALETTE[index % SECTOR_PALETTE.length] ?? "#60a5fa";
}

export interface PlanePoint {
  readonly x: number;
  readonly y: number;
}

/** A sector's position on the plane at a fractional week, along a smooth curve through its
 * weekly points (Catmull-Rom: it passes exactly through every week and bends gently between them,
 * so a jumpy series reads as a path rather than a zig-zag). The result is held inside the frame,
 * since the curve can swing slightly past a point. Returns null for an empty series. */
export function planeAt(plane: readonly PlanePoint[], week: number): PlanePoint | null {
  const last = plane.length - 1;
  if (last < 0) return null;
  const clamped = Math.min(Math.max(week, 0), last);
  const index = Math.floor(clamped);
  const t = clamped - index;
  const p1 = plane[index];
  const p2 = plane[Math.min(index + 1, last)];
  const p0 = plane[Math.max(index - 1, 0)];
  const p3 = plane[Math.min(index + 2, last)];
  if (!p0 || !p1 || !p2 || !p3) return null;
  const along = (a: number, b: number, c: number, d: number): number => {
    const value =
      0.5 *
      (2 * b +
        (-a + c) * t +
        (2 * a - 5 * b + 4 * c - d) * t * t +
        (-a + 3 * b - 3 * c + d) * t * t * t);
    return Math.min(Math.max(value, -PLANE_USABLE), PLANE_USABLE);
  };
  return {
    x: along(p0.x, p1.x, p2.x, p3.x),
    y: along(p0.y, p1.y, p2.y, p3.y),
  };
}
