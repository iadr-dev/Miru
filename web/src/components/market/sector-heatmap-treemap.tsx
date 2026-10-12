"use client";

import { motion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import { heatColor } from "@/lib/heat-color";
import { DIRECTION_COLOR, DIRECTION_GLYPH, directionOf } from "@/lib/signed";
import type { SectorHeatmapRow, SectorHeatmapSizeBy } from "@/types/market";

/** One laid-out tile: the source row plus a normalized [0,1] rectangle within the treemap's own
 * content box (converted to percentages at render time so the layout is pure CSS, no fixed px). */
interface HeatmapTile {
  row: SectorHeatmapRow;
  x: number;
  y: number;
  width: number;
  height: number;
}

/** A minimal squarified-treemap layout (Bruls et al.) over `total_turnover` (falling back to
 * `stock_count` when turnover weighting wasn't available for every industry that day —
 * `SectorHeatmapRow.total_turnover` is `null` in that case, never partially summed). Self-rolled
 * rather than pulling in a treemap-charting dependency: the layout is ~40 lines and every other
 * visual (color scale, tile chrome, a11y) is bespoke to this design anyway. */
/** The unit-square layout box `squarify` lays tiles out in — a purely arbitrary aspect ratio
 * (5:3), chosen only because it's a reasonable wide-card shape; every render position is a
 * percentage of these, so the box's real pixel size is whatever the surrounding card gives it. */
const LAYOUT_WIDTH = 100;
const LAYOUT_HEIGHT = 60;

interface SizedItem {
  row: SectorHeatmapRow;
  size: number;
}

/** The worst (largest) width:height ratio any tile in `row` would get if laid out at `length`
 * along the shorter side of the remaining `width x height` box — the classic squarified-treemap
 * "worst aspect ratio" measure (Bruls, Huizing, van Wijk 1999) used to decide how many items
 * belong in the current row before starting a new one. */
function worstRatio(row: readonly SizedItem[], length: number): number {
  const rowTotal = row.reduce((sum, item) => sum + item.size, 0);
  const rowThickness = rowTotal / length;
  let worst = 0;
  for (const item of row) {
    const itemLength = item.size / rowThickness;
    const ratio = Math.max(itemLength / rowThickness, rowThickness / itemLength);
    if (ratio > worst) worst = ratio;
  }
  return worst;
}

function layoutRow(
  row: readonly SizedItem[],
  x: number,
  y: number,
  w: number,
  h: number,
  tiles: HeatmapTile[],
): void {
  const rowTotal = row.reduce((sum, item) => sum + item.size, 0);
  const isWide = w >= h;
  const thickness = isWide ? rowTotal / h : rowTotal / w;
  let offset = isWide ? y : x;
  for (const item of row) {
    const extent = item.size / (thickness || 1);
    if (isWide) {
      tiles.push({ row: item.row, x, y: offset, width: thickness, height: extent });
      offset += extent;
    } else {
      tiles.push({ row: item.row, x: offset, y, width: extent, height: thickness });
      offset += extent;
    }
  }
}

/** Picks the tile-size value for `sizeBy` — falls back to `stock_count` whenever the requested
 * metric is `null` for this industry (turnover-weighting/market-value coverage gaps, same
 * never-partially-summed reasoning `SectorHeatmapRow.total_turnover`'s own docstring gives),
 * never silently zero-sizing a tile just because one metric was unavailable. */
function tileSizeValue(row: SectorHeatmapRow, sizeBy: SectorHeatmapSizeBy): number {
  switch (sizeBy) {
    case "market_value":
      return row.total_market_value ?? row.stock_count;
    case "volume":
      return row.total_volume || row.stock_count;
    case "turnover":
      return row.total_turnover ?? row.stock_count;
    default:
      return row.stock_count;
  }
}

/** A squarified-treemap layout (Bruls et al.) over the caller's chosen `sizeBy` metric (falling
 * back to `stock_count` per-tile when that metric is unavailable for an industry — see
 * `tileSizeValue`). At each step it grows the current row one item at a time along the box's
 * shorter side, stopping the instant the worst aspect ratio in that row would get worse by
 * adding another item — the standard squarify recursion, not a fixed-threshold heuristic (a
 * naive threshold degenerates into one giant stacked-bar row for a power-law-shaped
 * distribution, which real turnover/market-value data is). Self-rolled rather than pulling in a
 * treemap-charting dependency: every other visual (color scale, tile chrome, a11y) here is
 * bespoke to this design anyway. */
export function squarify(
  rows: readonly SectorHeatmapRow[],
  width: number,
  height: number,
  sizeBy: SectorHeatmapSizeBy = "turnover",
): HeatmapTile[] {
  const sized: SizedItem[] = rows
    .map((row) => ({ row, size: Math.max(tileSizeValue(row, sizeBy), 0.0001) }))
    .sort((a, b) => b.size - a.size);
  const total = sized.reduce((sum, item) => sum + item.size, 0);
  if (total <= 0 || sized.length === 0) return [];

  const scale = (width * height) / total;
  const scaled = sized.map((item) => ({ ...item, size: item.size * scale }));
  const tiles: HeatmapTile[] = [];

  let remaining = scaled;
  let x = 0;
  let y = 0;
  let w = width;
  let h = height;
  while (remaining.length > 0) {
    const shortSide = Math.min(w, h);
    let row: SizedItem[] = [remaining[0] as SizedItem];
    let rest = remaining.slice(1);
    let bestRatio = worstRatio(row, shortSide);
    while (rest.length > 0) {
      const candidateRow = [...row, rest[0] as SizedItem];
      const candidateRatio = worstRatio(candidateRow, shortSide);
      if (candidateRatio > bestRatio) break;
      row = candidateRow;
      rest = rest.slice(1);
      bestRatio = candidateRatio;
    }
    remaining = rest;
    const rowTotal = row.reduce((sum, item) => sum + item.size, 0);
    if (w >= h) {
      const rowWidth = rowTotal / h;
      layoutRow(row, x, y, rowWidth, h, tiles);
      x += rowWidth;
      w -= rowWidth;
    } else {
      const rowHeight = rowTotal / w;
      layoutRow(row, x, y, w, rowHeight, tiles);
      y += rowHeight;
      h -= rowHeight;
    }
  }
  return tiles;
}

/** Picks a text-size tier from a tile's rendered area (in layout-box units) — a small industry
 * tile showing its full label+% at the same font size as a huge one either overflows or wastes
 * space, so the label scales with the tile the way finviz's own map does. */
function tileTextSize(tile: HeatmapTile): { label: string; pct: string } {
  const area = tile.width * tile.height;
  if (area > 400) return { label: "text-[20px]", pct: "text-[15px]" };
  if (area > 120) return { label: "text-[14px]", pct: "text-[12px]" };
  if (area > 40) return { label: "text-[11px]", pct: "text-[10px]" };
  return { label: "text-[9px]", pct: "text-[8px]" };
}

/** The tooltip's size-metric line — shows whichever metric `sizeBy` actually sizes the tile by
 * (成交額/成交量/市值), never always turnover regardless of the selector, and only when that
 * metric has a real value for this industry (never a fabricated 0). */
function sizeMetricLabel(
  row: SectorHeatmapRow,
  sizeBy: SectorHeatmapSizeBy,
  t: ReturnType<typeof useTranslations>,
  locale: string,
): ReactNode {
  if (sizeBy === "volume") {
    return (
      <p className="mt-1.5 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
        {t("volume", { value: formatNumber(row.total_volume, locale, { decimals: 0 }) })}
      </p>
    );
  }
  if (sizeBy === "market_value") {
    if (row.total_market_value === null) return null;
    return (
      <p className="mt-1.5 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
        {t("marketValue", {
          value: formatNumber(row.total_market_value / 1_0000_0000, locale, { decimals: 1 }),
        })}
      </p>
    );
  }
  if (row.total_turnover === null) return null;
  return (
    <p className="mt-1.5 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
      {t("turnover", {
        value: formatNumber(row.total_turnover / 1_0000_0000, locale, { decimals: 1 }),
      })}
    </p>
  );
}

/**
 * 產業熱力圖 — one tile per industry, sized by `total_turnover` (or `stock_count` when turnover
 * weighting was unavailable) and colored by `avg_pct_change` via the `--up`/`--down` scale
 * (never color-alone: every tile also carries ▲/▼ + sign text and an `aria-label`). Tiles are
 * `role="button"` with roving-tabindex spatial arrow-nav (web/CLAUDE.md §7) — arrow keys move
 * focus to the geometrically-nearest tile in that direction, Enter/Space toggles selection.
 */
export function SectorHeatmapTreemap({
  rows,
  sizeBy = "turnover",
  selected,
  onSelect,
  updatedAt,
}: {
  readonly rows: readonly SectorHeatmapRow[];
  /** Which metric sizes each tile — mirrors `get_sector_heatmap`'s own `size_by` input, defaults
   * to `"turnover"` (this component's original, only behavior before the 排序方式 selector). */
  readonly sizeBy?: SectorHeatmapSizeBy;
  readonly selected: string | null;
  readonly onSelect: (industry: string | null) => void;
  /** `query.dataUpdatedAt` from the caller — every tile flashes once, at the SAME duration,
   * whenever fresh data lands (web/CLAUDE.md §4 "non-trading motion": a bigger % move never
   * gets a bigger/faster animation, so this is keyed only on "did data just update", never on
   * `avg_pct_change`'s magnitude). */
  readonly updatedAt?: number;
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.sectorHeatmap");
  const containerRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [flashKey, setFlashKey] = useState(0);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  // Fixed layout box in an arbitrary unit square — rendered as percentages, so the actual pixel
  // size is whatever the surrounding card gives it (a real `ResizeObserver`-driven layout isn't
  // needed since percentages already reflow with the container).
  const tiles = useMemo(() => squarify(rows, LAYOUT_WIDTH, LAYOUT_HEIGHT, sizeBy), [rows, sizeBy]);

  // Bumps once per real data refresh (not on every render) so every tile's flash animation
  // below re-triggers together — `motion/react`'s `key` prop restarts the animation from its
  // `initial` state, which is how a uniform, non-looping one-shot flash is expressed here.
  useEffect(() => {
    if (updatedAt !== undefined) setFlashKey((k) => k + 1);
  }, [updatedAt]);

  if (tiles.length === 0) {
    return <p className="text-[13px] text-[var(--text-muted)]">{t("unavailable")}</p>;
  }

  function moveFocus(from: number, dx: number, dy: number): void {
    const current = tiles[from];
    if (!current) return;
    const currentCx = current.x + current.width / 2;
    const currentCy = current.y + current.height / 2;
    let bestIndex = from;
    let bestScore = Number.POSITIVE_INFINITY;
    tiles.forEach((tile, index) => {
      if (index === from) return;
      const cx = tile.x + tile.width / 2;
      const cy = tile.y + tile.height / 2;
      const ddx = cx - currentCx;
      const ddy = cy - currentCy;
      // Candidate must lie roughly in the requested direction.
      if (dx !== 0 && Math.sign(ddx) !== dx) return;
      if (dy !== 0 && Math.sign(ddy) !== dy) return;
      const score = ddx * ddx + ddy * ddy;
      if (score < bestScore) {
        bestScore = score;
        bestIndex = index;
      }
    });
    if (bestIndex !== from) {
      setFocusedIndex(bestIndex);
      containerRef.current
        ?.querySelectorAll<HTMLButtonElement>("[data-heatmap-tile]")
        [bestIndex]?.focus();
    }
  }

  return (
    <div
      ref={containerRef}
      role="group"
      aria-label={t("title")}
      className="relative w-full overflow-hidden rounded-[var(--radius-input)] aspect-[100/85] sm:aspect-[100/60]"
    >
      {tiles.map((tile, index) => {
        const { row } = tile;
        const dir = directionOf(row.avg_pct_change, 2);
        const arrow = DIRECTION_GLYPH[dir];
        const pctLabel = `${formatNumber(Math.abs(row.avg_pct_change), locale, { decimals: 2 })}%`;
        const direction = t(dir);
        const isSelected = selected === row.industry;
        const textSize = tileTextSize(tile);
        return (
          <div
            key={row.industry}
            data-heatmap-tile
            role="button"
            tabIndex={index === focusedIndex ? 0 : -1}
            aria-pressed={isSelected}
            aria-label={`${row.industry} ${direction} ${pctLabel}，${t("stockCount", { count: row.stock_count })}`}
            onFocus={() => setFocusedIndex(index)}
            onMouseEnter={() => setHoveredIndex(index)}
            onMouseLeave={() => setHoveredIndex((current) => (current === index ? null : current))}
            onClick={() => onSelect(isSelected ? null : row.industry)}
            onKeyDown={(event) => {
              switch (event.key) {
                case "ArrowLeft":
                  event.preventDefault();
                  moveFocus(index, -1, 0);
                  break;
                case "ArrowRight":
                  event.preventDefault();
                  moveFocus(index, 1, 0);
                  break;
                case "ArrowUp":
                  event.preventDefault();
                  moveFocus(index, 0, -1);
                  break;
                case "ArrowDown":
                  event.preventDefault();
                  moveFocus(index, 0, 1);
                  break;
                case "Enter":
                case " ":
                  event.preventDefault();
                  onSelect(isSelected ? null : row.industry);
                  break;
                default:
                  break;
              }
            }}
            className={cn(
              "absolute flex cursor-pointer flex-col items-center justify-center gap-0.5 p-1 text-center",
              "border-2 border-[var(--bg)] transition-[outline-color] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
              hoveredIndex === index
                ? "z-10 ring-2 ring-[var(--text)] ring-inset"
                : "overflow-hidden",
              isSelected ? "ring-2 ring-[var(--accent)] ring-inset" : null,
            )}
            style={{
              left: `${(tile.x / LAYOUT_WIDTH) * 100}%`,
              top: `${(tile.y / LAYOUT_HEIGHT) * 100}%`,
              width: `${(tile.width / LAYOUT_WIDTH) * 100}%`,
              height: `${(tile.height / LAYOUT_HEIGHT) * 100}%`,
              backgroundColor: heatColor(row.avg_pct_change),
            }}
          >
            <span
              className={cn(
                "heat-pill max-w-full truncate font-semibold text-[var(--text)] leading-tight",
                textSize.label,
              )}
            >
              {row.industry}
            </span>
            <span
              className={cn(
                "heat-pill font-num text-[var(--text)] tabular-nums leading-tight",
                textSize.pct,
              )}
            >
              {arrow}
              {pctLabel}
            </span>
            <motion.div
              key={flashKey}
              aria-hidden
              className="pointer-events-none absolute inset-0 bg-[var(--surface)]"
              initial={{ opacity: 0 }}
              // Same two-phase timing for every tile regardless of avg_pct_change — flash in
              // (dur.tick, ~140ms) then fade back out (dur.tick-fade, ~600ms), per
              // the live-tick flash contract.
              animate={{ opacity: [0, 0.35, 0] }}
              transition={{ duration: 0.74, times: [0, 0.19, 1], ease: "easeOut" }}
            />
            {hoveredIndex === index ? (
              <div
                role="tooltip"
                className={cn(
                  "pointer-events-none absolute z-20 w-44 rounded-[var(--radius-input)] border border-[var(--border)]",
                  "bg-[var(--surface)] p-2.5 text-left shadow-lg",
                  // Anchored to a small fixed corner of the TILE itself (never `top-full`/
                  // `bottom-full`, which measure from the tile's own — sometimes huge — edge:
                  // for a large tile like 電子工業 that placed the tooltip hundreds of px off
                  // the visible treemap). Flips corner based on which half of the layout box
                  // the tile sits in, so the tooltip stays inside the treemap's own bounds.
                  tile.y < LAYOUT_HEIGHT / 2 ? "top-2" : "bottom-2",
                  tile.x < LAYOUT_WIDTH / 2 ? "left-2" : "right-2",
                )}
              >
                <p className="font-semibold text-[12px] text-[var(--text)]">{row.industry}</p>
                <p className="mt-1 font-num text-[13px] text-[color:var(--text)] tabular-nums">
                  <span style={{ color: DIRECTION_COLOR[dir] }}>
                    {arrow}
                    {pctLabel}
                  </span>
                </p>
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">
                  {t("stockCount", { count: row.stock_count })}
                </p>
                <div className="mt-1.5 flex items-center gap-2 text-[11px]">
                  <span style={{ color: "var(--up)" }}>
                    ▲{formatNumber(row.advancing_count, locale)}
                  </span>
                  <span style={{ color: "var(--down)" }}>
                    ▼{formatNumber(row.declining_count, locale)}
                  </span>
                </div>
                {sizeMetricLabel(row, sizeBy, t, locale)}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
