"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import type { SectorIndustryWeightRow } from "@/types/stock/profile";

/** One laid-out tile: the source row plus a normalized rectangle within the treemap's own
 * content box (converted to percentages at render time, same mechanism as
 * `components/market/sector-heatmap-treemap.tsx`'s own `HeatmapTile`). */
interface WeightTile {
  row: SectorIndustryWeightRow;
  x: number;
  y: number;
  width: number;
  height: number;
}

// Same arbitrary 5:3 unit-square layout box `sector-heatmap-treemap.tsx` uses — every render
// position is a percentage of these, so the box's real pixel size is whatever the surrounding
// card gives it.
const LAYOUT_WIDTH = 100;
const LAYOUT_HEIGHT = 60;

interface SizedItem {
  row: SectorIndustryWeightRow;
  size: number;
}

/** The worst (largest) width:height ratio any tile in `row` would get if laid out at `length`
 * along the shorter side of the remaining box — identical squarified-treemap measure
 * `sector-heatmap-treemap.tsx::worstRatio` uses, duplicated here (not imported) since that
 * component's layout functions are coupled to `SectorHeatmapRow`, not a generic row type — see
 * this codebase's own convention of one treemap component per row shape (no `Treemap<T>`
 * abstraction exists; `stock-heatmap-treemap.tsx` is already a second sibling of this pattern). */
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
  tiles: WeightTile[],
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

/** A squarified-treemap layout (Bruls, Huizing, van Wijk 1999) sized directly by `market_weight`
 * — every row from `yfinance.Sector.industries` always has a real weight, so (unlike
 * `sector-heatmap-treemap.tsx`'s own `tileSizeValue`) no fallback metric is needed here. */
export function squarifyByWeight(
  rows: readonly SectorIndustryWeightRow[],
  width: number,
  height: number,
): WeightTile[] {
  const sized: SizedItem[] = rows
    .map((row) => ({ row, size: Math.max(row.market_weight, 0.0001) }))
    .sort((a, b) => b.size - a.size);
  const total = sized.reduce((sum, item) => sum + item.size, 0);
  if (total <= 0 || sized.length === 0) return [];

  const scale = (width * height) / total;
  const scaled = sized.map((item) => ({ ...item, size: item.size * scale }));
  const tiles: WeightTile[] = [];

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

function tileTextSize(tile: WeightTile): { label: string; pct: string } {
  const area = tile.width * tile.height;
  if (area > 400) return { label: "text-[20px]", pct: "text-[15px]" };
  if (area > 120) return { label: "text-[14px]", pct: "text-[12px]" };
  if (area > 40) return { label: "text-[11px]", pct: "text-[10px]" };
  return { label: "text-[9px]", pct: "text-[8px]" };
}

// A neutral categorical palette (`--chart-1..6`, cycled by tile index) — NEVER `--up`/`--down`.
// This treemap sizes tiles by market-weight COMPOSITION, not a price move; coloring it red/green
// would misleadingly imply a performance signal that doesn't exist here (web/OVERVIEW.md §4:
// 紅漲綠跌 applies only to price/%/score/candles).
const TILE_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

/**
 * 產業權重圖 — one tile per industry within this stock's own sector, sized by
 * `yfinance.Sector.industries`' real market-weight breakdown (e.g. Technology ->
 * Semiconductors ~38%, Software-Infrastructure ~21%, ...). A composition treemap, not a
 * performance heatmap — tiles use a neutral categorical palette, never 紅漲綠跌.
 */
export function SectorIndustriesTreemap({
  rows,
}: {
  readonly rows: readonly SectorIndustryWeightRow[];
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("usStock.sectorPeers");
  const containerRef = useRef<HTMLDivElement>(null);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const tiles = useMemo(() => squarifyByWeight(rows, LAYOUT_WIDTH, LAYOUT_HEIGHT), [rows]);

  if (tiles.length === 0) return null;

  return (
    <div
      ref={containerRef}
      role="group"
      aria-label={t("industriesTreemapTitle")}
      className="relative aspect-[100/85] w-full overflow-hidden rounded-[var(--radius-input)] sm:aspect-[100/60]"
    >
      {tiles.map((tile, index) => {
        const { row } = tile;
        const pctLabel = `${formatNumber(row.market_weight * 100, locale, { decimals: 1 })}%`;
        const textSize = tileTextSize(tile);
        const color = TILE_COLORS[index % TILE_COLORS.length];
        return (
          <div
            key={row.industry_key}
            role="img"
            aria-label={`${row.name} ${pctLabel}`}
            onMouseEnter={() => setHoveredIndex(index)}
            onMouseLeave={() => setHoveredIndex((current) => (current === index ? null : current))}
            className={cn(
              "absolute flex flex-col items-center justify-center gap-0.5 overflow-hidden p-1 text-center",
              "border-2 border-[var(--bg)]",
              hoveredIndex === index ? "z-10 ring-2 ring-[var(--text)] ring-inset" : null,
            )}
            style={{
              left: `${(tile.x / LAYOUT_WIDTH) * 100}%`,
              top: `${(tile.y / LAYOUT_HEIGHT) * 100}%`,
              width: `${(tile.width / LAYOUT_WIDTH) * 100}%`,
              height: `${(tile.height / LAYOUT_HEIGHT) * 100}%`,
              backgroundColor: color,
            }}
          >
            <span
              className={cn(
                "heat-pill max-w-full truncate font-semibold text-[var(--text)] leading-tight",
                textSize.label,
              )}
            >
              {row.name}
            </span>
            <span
              className={cn(
                "heat-pill font-num text-[var(--text)] tabular-nums leading-tight",
                textSize.pct,
              )}
            >
              {pctLabel}
            </span>
            {hoveredIndex === index ? (
              <div
                role="tooltip"
                className={cn(
                  "pointer-events-none absolute z-20 w-44 rounded-[var(--radius-input)] border border-[var(--border)]",
                  "bg-[var(--surface)] p-2.5 text-left shadow-lg",
                  tile.y < LAYOUT_HEIGHT / 2 ? "top-2" : "bottom-2",
                  tile.x < LAYOUT_WIDTH / 2 ? "left-2" : "right-2",
                )}
              >
                <p className="font-semibold text-[12px] text-[var(--text)]">{row.name}</p>
                <p className="mt-1 font-num text-[13px] text-[var(--text)] tabular-nums">
                  {t("industryWeight", { value: pctLabel })}
                </p>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
