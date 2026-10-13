/**
 * Sector badge icon system for 板塊輪動 (`SectorRotationScene`) — one shape-ops definition per
 * industry, rendered by TWO independent renderers so the exact same icon geometry appears both
 * in the 2D `RankPanel` list (`iconToSvgElement`, real SVG so it inherits `currentColor` /
 * `aria-hidden` like every other icon in this app) and on the 3D `SectorBadge` sprite
 * (`drawIconOnCanvas`, painted onto a `CanvasTexture` — three.js has no native vector-icon
 * renderer, so the badge's icon layer is a small offscreen canvas billboard).
 *
 * A shape-ops definition is a plain array of primitive ops on a 0-24 coordinate grid (the same
 * viewBox convention Phosphor/most icon sets use) — `rect`/`line`/`circle`/`polyline`, nothing
 * fancier. Every icon here is intentionally a few basic shapes, not a traced illustration: at
 * badge scale (~20px) fine detail disappears anyway, and keeping every icon in the same
 * primitive vocabulary is what makes ONE canvas-drawing function able to render all of them
 * (see `drawIconOnCanvas`) — the same reason the reference spec calls for a single source of
 * shape-ops rather than 8 hand-drawn SVG paths.
 *
 * Icon COLOR is never decorative here — `SectorBadge`/`RankPanel` both pass the sector's current
 * quadrant color (領先 red / 走弱 yellow / 落後 green / 轉強 blue) as `color`, so a colorblind
 * user can still tell sectors apart by shape, and a sighted user gets the quadrant reinforced
 * twice (badge fill + icon stroke) rather than once.
 *
 * Adding a new sector icon: append a `ShapeOp[]` array using only `rect`/`line`/`circle`/
 * `polyline` coordinates in the 0-24 grid, then add its key to `SECTOR_ICON_KEYS` and a case to
 * `sectorIconKeyForIndustry` (or pass the key directly — that lookup is just this file's own
 * convenience mapping from a FinMind industry id to an icon, not required by the renderers).
 */
import type { ReactElement } from "react";

export type ShapeOp =
  | { type: "rect"; x: number; y: number; w: number; h: number; rx?: number }
  | { type: "line"; x1: number; y1: number; x2: number; y2: number }
  | { type: "circle"; cx: number; cy: number; r: number; fill?: boolean }
  | { type: "polyline"; points: readonly [number, number][] };

export const SECTOR_ICON_KEYS = [
  "server-rack",
  "chip",
  "circuit-board",
  "ship",
  "candlesticks",
  "factory",
  "pulse",
  "crane",
] as const;
export type SectorIconKey = (typeof SECTOR_ICON_KEYS)[number];

/** Server rack — AI伺服器/ComputerPeripheralEquipment: three stacked unit slots + a status dot
 * on the right of the top slot. */
const SERVER_RACK: ShapeOp[] = [
  { type: "rect", x: 4, y: 4, w: 16, h: 4.5, rx: 1 },
  { type: "rect", x: 4, y: 10, w: 16, h: 4.5, rx: 1 },
  { type: "rect", x: 4, y: 16, w: 16, h: 4.5, rx: 1 },
  { type: "circle", cx: 17, cy: 6.25, r: 0.9, fill: true },
];

/** Chip — Semiconductor: a square die with four pins per side. */
const CHIP: ShapeOp[] = [
  { type: "rect", x: 7, y: 7, w: 10, h: 10, rx: 1 },
  { type: "line", x1: 9, y1: 3, x2: 9, y2: 7 },
  { type: "line", x1: 12, y1: 3, x2: 12, y2: 7 },
  { type: "line", x1: 15, y1: 3, x2: 15, y2: 7 },
  { type: "line", x1: 9, y1: 17, x2: 9, y2: 21 },
  { type: "line", x1: 12, y1: 17, x2: 12, y2: 21 },
  { type: "line", x1: 15, y1: 17, x2: 15, y2: 21 },
  { type: "line", x1: 3, y1: 9, x2: 7, y2: 9 },
  { type: "line", x1: 3, y1: 12, x2: 7, y2: 12 },
  { type: "line", x1: 3, y1: 15, x2: 7, y2: 15 },
  { type: "line", x1: 17, y1: 9, x2: 21, y2: 9 },
  { type: "line", x1: 17, y1: 12, x2: 21, y2: 12 },
  { type: "line", x1: 17, y1: 15, x2: 21, y2: 15 },
];

/** Circuit board — IC設計/Electronic/CommunicationsInternet: a board outline with a diagonal
 * trace between two solder-point nodes. */
const CIRCUIT_BOARD: ShapeOp[] = [
  { type: "rect", x: 3, y: 3, w: 18, h: 18, rx: 2 },
  { type: "line", x1: 7, y1: 17, x2: 17, y2: 7 },
  { type: "circle", cx: 7, cy: 17, r: 1.6, fill: true },
  { type: "circle", cx: 17, cy: 7, r: 1.6, fill: true },
];

/** Ship — ShippingTransportation: a trapezoid hull + a single mast. */
const SHIP: ShapeOp[] = [
  {
    type: "polyline",
    points: [
      [4, 16],
      [6, 21],
      [18, 21],
      [20, 16],
      [4, 16],
    ],
  },
  { type: "line", x1: 12, y1: 16, x2: 12, y2: 4 },
  { type: "line", x1: 12, y1: 5, x2: 18, y2: 8 },
];

/** Candlesticks — FinancialInsurance: two K-line candles, one up-bodied, one down-bodied. */
const CANDLESTICKS: ShapeOp[] = [
  { type: "line", x1: 8, y1: 3, x2: 8, y2: 9 },
  { type: "rect", x: 6, y: 9, w: 4, h: 7, rx: 0.5 },
  { type: "line", x1: 8, y1: 16, x2: 8, y2: 21 },
  { type: "line", x1: 16, y1: 4, x2: 16, y2: 10 },
  { type: "rect", x: 14, y: 10, w: 4, h: 6, rx: 0.5 },
  { type: "line", x1: 16, y1: 16, x2: 16, y2: 20 },
];

/** Factory — IronSteel/傳産-style heavy industry: a building silhouette + a chimney. */
const FACTORY: ShapeOp[] = [
  {
    type: "polyline",
    points: [
      [3, 21],
      [3, 12],
      [9, 15],
      [9, 9],
      [15, 12],
      [15, 21],
    ],
  },
  { type: "rect", x: 3, y: 21, w: 12, h: 0.01 },
  { type: "rect", x: 16, y: 5, w: 3, h: 8 },
  { type: "line", x1: 3, y1: 21, x2: 21, y2: 21 },
];

/** Pulse — BiotechnologyMedicalCare: a flat EKG line with one heartbeat spike. */
const PULSE: ShapeOp[] = [
  {
    type: "polyline",
    points: [
      [2, 12],
      [7, 12],
      [9, 5],
      [12, 19],
      [14, 12],
      [22, 12],
    ],
  },
];

/** Crane — BuildingMaterialConstruction: a vertical mast + a horizontal jib arm with a hook. */
const CRANE: ShapeOp[] = [
  { type: "line", x1: 6, y1: 21, x2: 6, y2: 4 },
  { type: "line", x1: 6, y1: 4, x2: 20, y2: 4 },
  { type: "line", x1: 6, y1: 8, x2: 16, y2: 4 },
  { type: "line", x1: 18, y1: 4, x2: 18, y2: 10 },
  { type: "line", x1: 3, y1: 21, x2: 9, y2: 21 },
];

const SHAPE_OPS_BY_KEY: Record<SectorIconKey, readonly ShapeOp[]> = {
  "server-rack": SERVER_RACK,
  chip: CHIP,
  "circuit-board": CIRCUIT_BOARD,
  ship: SHIP,
  candlesticks: CANDLESTICKS,
  factory: FACTORY,
  pulse: PULSE,
  crane: CRANE,
};

/** Maps a FinMind industry `data_id` (`SECTOR_RRG_INDEX_IDS` on the backend) to its icon key —
 * this file's own convenience lookup, not required by the two renderers below (which take a key
 * directly). Falls back to `circuit-board` (a generic electronics/board mark) for any id not yet
 * mapped, so a newly-added backend index never crashes the icon layer while its own icon is
 * pending. */
export function sectorIconKeyForIndustry(industryId: string): SectorIconKey {
  switch (industryId) {
    case "Semiconductor":
      return "chip";
    case "Electronic":
    case "ElectronicPartsComponents":
    case "CommunicationsInternet":
      return "circuit-board";
    case "ComputerPeripheralEquipment":
      return "server-rack";
    case "FinancialInsurance":
      return "candlesticks";
    case "ShippingTransportation":
      return "ship";
    case "BiotechnologyMedicalCare":
      return "pulse";
    case "BuildingMaterialConstruction":
      return "crane";
    case "IronSteel":
      return "factory";
    default:
      return "circuit-board";
  }
}

function svgChildForOp(op: ShapeOp, index: number): ReactElement {
  switch (op.type) {
    case "rect":
      return (
        <rect
          key={index}
          x={op.x}
          y={op.y}
          width={op.w}
          height={op.h}
          rx={op.rx ?? 0}
          fill="none"
        />
      );
    case "line":
      return <line key={index} x1={op.x1} y1={op.y1} x2={op.x2} y2={op.y2} />;
    case "circle":
      return (
        <circle
          key={index}
          cx={op.cx}
          cy={op.cy}
          r={op.r}
          fill={op.fill ? "currentColor" : "none"}
        />
      );
    case "polyline":
      return (
        <polyline
          key={index}
          points={op.points.map(([x, y]) => `${x},${y}`).join(" ")}
          fill="none"
        />
      );
  }
}

/** Renders one icon as a React SVG element — `stroke="currentColor" fill="none"` (except the
 * few filled dots/nodes each icon uses for a pin/solder-point/status light), 24x24 viewBox,
 * matching every other icon in this app's `stroke`-driven convention (Phosphor `weight="regular"`
 * — see web/OVERVIEW.md §2). Used in `RankPanel`'s list rows. */
export function iconToSvgElement(key: SectorIconKey, size = 16): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      role="presentation"
    >
      {SHAPE_OPS_BY_KEY[key].map((op, index) => svgChildForOp(op, index))}
    </svg>
  );
}
