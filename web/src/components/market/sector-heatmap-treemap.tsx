"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";
import type { SectorHeatmapRow } from "@/types/market";

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

/** A squarified-treemap layout (Bruls et al.) over `total_turnover` (falling back to
 * `stock_count` when turnover weighting wasn't available for every industry that day —
 * `SectorHeatmapRow.total_turnover` is `null` in that case, never partially summed). At each
 * step it grows the current row one item at a time along the box's shorter side, stopping the
 * instant the worst aspect ratio in that row would get worse by adding another item — the
 * standard squarify recursion, not a fixed-threshold heuristic (a naive threshold degenerates
 * into one giant stacked-bar row for a power-law-shaped distribution, which real turnover data
 * is). Self-rolled rather than pulling in a treemap-charting dependency: every other visual
 * (color scale, tile chrome, a11y) here is bespoke to this design anyway. */
export function squarify(
  rows: readonly SectorHeatmapRow[],
  width: number,
  height: number,
): HeatmapTile[] {
  const sized: SizedItem[] = rows
    .map((row) => ({ row, size: Math.max(row.total_turnover ?? row.stock_count, 0.0001) }))
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

/** Maps `avg_pct_change` to an `--up`/`--down` intensity — weak tint near zero, full-strength
 * token past ±3% (a calm, non-magnitude-scaled clamp, not a per-tile computed hex: only the
 * *mix ratio* between the two existing tokens varies, never a new color). */
function tileColor(pctChange: number): string {
  const magnitude = Math.min(Math.abs(pctChange) / 3, 1);
  const token = pctChange >= 0 ? "--up" : "--down";
  const weakToken = pctChange >= 0 ? "--up-weak" : "--down-weak";
  const pct = Math.round(30 + magnitude * 70);
  return `color-mix(in srgb, var(${token}) ${pct}%, var(${weakToken}))`;
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
  selected,
  onSelect,
}: {
  readonly rows: readonly SectorHeatmapRow[];
  readonly selected: string | null;
  readonly onSelect: (industry: string | null) => void;
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.sectorHeatmap");
  const containerRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);

  // Fixed layout box in an arbitrary unit square — rendered as percentages, so the actual pixel
  // size is whatever the surrounding card gives it (a real `ResizeObserver`-driven layout isn't
  // needed since percentages already reflow with the container).
  const tiles = useMemo(() => squarify(rows, LAYOUT_WIDTH, LAYOUT_HEIGHT), [rows]);

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
      className="relative w-full overflow-hidden rounded-[var(--radius-input)]"
      style={{ aspectRatio: `${LAYOUT_WIDTH} / ${LAYOUT_HEIGHT}` }}
    >
      {tiles.map((tile, index) => {
        const { row } = tile;
        const up = row.avg_pct_change >= 0;
        const arrow = up ? "▲" : row.avg_pct_change < 0 ? "▼" : "";
        const sign = up && row.avg_pct_change > 0 ? "+" : "";
        const pctLabel = `${sign}${formatNumber(row.avg_pct_change, locale, { decimals: 2 })}%`;
        const direction = up ? t("up") : row.avg_pct_change < 0 ? t("down") : t("flat");
        const isSelected = selected === row.industry;
        return (
          <div
            key={row.industry}
            data-heatmap-tile
            role="button"
            tabIndex={index === focusedIndex ? 0 : -1}
            aria-pressed={isSelected}
            aria-label={`${row.industry} ${direction} ${pctLabel}，${t("stockCount", { count: row.stock_count })}`}
            onFocus={() => setFocusedIndex(index)}
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
              "absolute flex cursor-pointer flex-col justify-between overflow-hidden rounded-[4px] p-1.5 text-left",
              "border transition-[outline-color] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
              isSelected ? "border-[var(--accent)]" : "border-[var(--surface)]",
            )}
            style={{
              left: `${(tile.x / LAYOUT_WIDTH) * 100}%`,
              top: `${(tile.y / LAYOUT_HEIGHT) * 100}%`,
              width: `${(tile.width / LAYOUT_WIDTH) * 100}%`,
              height: `${(tile.height / LAYOUT_HEIGHT) * 100}%`,
              backgroundColor: tileColor(row.avg_pct_change),
            }}
          >
            <span className="truncate font-medium text-[11px] text-[color:#0e1319]">
              {row.industry}
            </span>
            <div className="flex items-end justify-between gap-1">
              <span className="font-num text-[12px] text-[color:#0e1319] tabular-nums">
                {arrow}
                {pctLabel}
              </span>
              <span className="font-num text-[10px] text-[color:#0e1319]/70 tabular-nums">
                {row.stock_count}
              </span>
            </div>
            <div className="flex h-1 w-full overflow-hidden rounded-full bg-[color:#0e1319]/15">
              <div
                className="h-full bg-[var(--up)]"
                style={{
                  width: `${(row.advancing_count / Math.max(row.advancing_count + row.declining_count, 1)) * 100}%`,
                }}
              />
              <div
                className="h-full bg-[var(--down)]"
                style={{
                  width: `${(row.declining_count / Math.max(row.advancing_count + row.declining_count, 1)) * 100}%`,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
