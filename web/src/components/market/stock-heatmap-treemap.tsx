"use client";

import { motion } from "motion/react";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { formatNumber } from "@/app/[locale]/(app)/chat/cards/card-format";
import { cn } from "@/lib/cn";
import { heatColor } from "@/lib/heat-color";
import { squarifyGeneric, type TreemapTile } from "@/lib/treemap";
import type { SectorHeatmapSizeBy, StockHeatmapRow } from "@/types/market";

// Same arbitrary wide-card layout box `SectorHeatmapTreemap` uses — every render position is a
// percentage of these, so the box's real pixel size is whatever the surrounding card gives it.
const LAYOUT_WIDTH = 100;
const LAYOUT_HEIGHT = 60;

// How many of an industry's biggest stocks get their own tile inside that industry's region —
// the nested structure this component now uses: an OUTER squarify pass lays out one region per
// industry (sized by that industry's total `sizeBy`, exactly like `SectorHeatmapTreemap`'s own
// industry tiles), then an INNER squarify pass splits each region into its top
// `STOCKS_PER_INDUSTRY_BOX` stocks. Capping at 2 is what actually fixes the "long thin rectangle"
// problem a flat 150-stock ranking had (confirmed live across several sizing attempts — rank-
// bounded weights, sqrt damping, halved collapsed counts — none of which reliably avoided thin
// tiles because squarify was still choosing among 20-40 comparably-sized competing items in one
// wide-but-short box): 2 items splitting a region that's already close to square almost always
// stay close to square themselves, regardless of how skewed their real value gap is.
const STOCKS_PER_INDUSTRY_BOX = 2;

/** The raw `sizeBy` metric for one stock — falls back to `volume` whenever the requested metric
 * is `null`, never silently zero-sizing a tile just because one metric was unavailable. */
function tileRawValue(row: StockHeatmapRow, sizeBy: SectorHeatmapSizeBy): number {
  switch (sizeBy) {
    case "market_value":
      return row.market_value ?? row.volume;
    case "volume":
      return row.volume;
    case "turnover":
      return row.turnover ?? row.volume;
    default:
      return row.volume;
  }
}

interface IndustryGroup {
  industry: string;
  totalSize: number;
  stocks: readonly StockHeatmapRow[]; // every stock present for this industry, sorted desc
}

/** Groups the flat `get_stock_heatmap` ranking by `industry` — each group's `totalSize` is the
 * SUM of every one of its stocks' `sizeBy` metric (not just the ones that get their own tile),
 * so a region's area reflects the industry's whole footprint within this already-top-N-capped
 * stock list, the same "industry region size = aggregate of its members" idea
 * `SectorHeatmapTreemap` expresses from `get_sector_heatmap`'s own real aggregate — this
 * component derives it from the flat stock ranking instead, since `get_stock_heatmap` doesn't
 * return per-industry aggregates itself. */
export function groupByIndustry(
  rows: readonly StockHeatmapRow[],
  sizeBy: SectorHeatmapSizeBy,
): IndustryGroup[] {
  const byIndustry = new Map<string, StockHeatmapRow[]>();
  for (const row of rows) {
    const list = byIndustry.get(row.industry);
    if (list) {
      list.push(row);
    } else {
      byIndustry.set(row.industry, [row]);
    }
  }
  return [...byIndustry.entries()].map(([industry, stocks]) => {
    const sorted = [...stocks].sort((a, b) => tileRawValue(b, sizeBy) - tileRawValue(a, sizeBy));
    const totalSize = sorted.reduce((sum, row) => sum + tileRawValue(row, sizeBy), 0);
    return { industry, totalSize, stocks: sorted };
  });
}

/** The nested two-level layout: an outer squarify pass over industry groups (sized by
 * `totalSize`, i.e. `sizeBy`), then an inner squarify pass within each industry's own region over
 * just its top `STOCKS_PER_INDUSTRY_BOX` stocks BY `sizeBy` — but sized by `|pct_change|`, not
 * `sizeBy` again: which stocks EARN a tile is still ranked by turnover/market-cap/volume, but the
 * tile's own AREA reflects how much that stock actually moved that day, so the day's biggest
 * mover visibly stands out rather than whichever stock merely has the biggest market cap. Safe
 * undamped here since at most 2 items ever compete for one region, never the 20-40-item
 * competition that made an earlier flat ranking produce thin tiles. `maxIndustries` caps how many
 * industry regions are laid out at all (the collapsed inline view's readability cut — same idea
 * `SectorHeatmapPanel` already applies by slicing `data.industries` before its own treemap, just
 * computed here since the grouping has to happen before slicing can pick "the biggest N
 * industries" rather than an arbitrary N stocks that might all land in the same 2-3
 * industries). */
export function layoutNestedTiles(
  rows: readonly StockHeatmapRow[],
  sizeBy: SectorHeatmapSizeBy,
  maxIndustries: number | undefined,
): TreemapTile<StockHeatmapRow>[] {
  const groups = groupByIndustry(rows, sizeBy).sort((a, b) => b.totalSize - a.totalSize);
  const shown = maxIndustries !== undefined ? groups.slice(0, maxIndustries) : groups;
  const industryTiles = squarifyGeneric(shown, (g) => g.totalSize, LAYOUT_WIDTH, LAYOUT_HEIGHT);

  return industryTiles.flatMap((industryTile) => {
    // Which stocks EARN a tile in this industry's region is still `sizeBy`-ranked (its biggest
    // constituents by turnover/market-cap/volume) — but their tile AREA inside that region is
    // sized by |pct_change|, so the stock that moved the most that day visibly stands out, not
    // just whichever happens to be bigger by market cap.
    const topStocks = industryTile.item.stocks.slice(0, STOCKS_PER_INDUSTRY_BOX);
    const innerTiles = squarifyGeneric(
      topStocks,
      (row) => Math.abs(row.pct_change),
      industryTile.width,
      industryTile.height,
    );
    return innerTiles.map((tile) => ({
      ...tile,
      x: tile.x + industryTile.x,
      y: tile.y + industryTile.y,
    }));
  });
}

/** Picks a text-size tier from a tile's rendered area — same tiering
 * `sector-heatmap-treemap.tsx`'s own `tileTextSize` uses (with a small symbol-line tier added,
 * since a stock tile shows name + ticker on two lines where an industry tile shows only one
 * label line). */
function tileTextSize(tile: TreemapTile<StockHeatmapRow>): {
  name: string;
  symbol: string;
  pct: string;
} {
  const area = tile.width * tile.height;
  if (area > 400) return { name: "text-[20px]", symbol: "text-[13px]", pct: "text-[15px]" };
  if (area > 120) return { name: "text-[14px]", symbol: "text-[11px]", pct: "text-[12px]" };
  if (area > 40) return { name: "text-[11px]", symbol: "text-[9px]", pct: "text-[10px]" };
  return { name: "text-[9px]", symbol: "text-[7.5px]", pct: "text-[8px]" };
}

function sizeMetricLabel(
  row: StockHeatmapRow,
  sizeBy: SectorHeatmapSizeBy,
  t: ReturnType<typeof useTranslations>,
  locale: string,
): ReactNode {
  if (sizeBy === "volume") {
    return (
      <p className="mt-1.5 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
        {t("volume", { value: formatNumber(row.volume, locale, { decimals: 0 }) })}
      </p>
    );
  }
  if (sizeBy === "market_value") {
    if (row.market_value === null) return null;
    return (
      <p className="mt-1.5 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
        {t("marketValue", {
          value: formatNumber(row.market_value / 1_0000_0000, locale, { decimals: 1 }),
        })}
      </p>
    );
  }
  if (row.turnover === null) return null;
  return (
    <p className="mt-1.5 font-num text-[10.5px] text-[var(--text-muted)] tabular-nums">
      {t("turnover", { value: formatNumber(row.turnover / 1_0000_0000, locale, { decimals: 1 }) })}
    </p>
  );
}

/**
 * 股票熱力圖 — a two-level nested treemap: an outer region per industry (sized/positioned exactly
 * like `SectorHeatmapTreemap`'s own industry tiles), each subdivided into its top
 * `STOCKS_PER_INDUSTRY_BOX` stocks (see `layoutNestedTiles`). This replaces an earlier flat
 * top-150 ranking — that approach kept producing long thin tiles no matter how the sizing was
 * damped/bounded, because squarify was choosing among too many comparably-sized competing items
 * in one wide-but-short box; nesting means at most 2 items ever compete for a given region, which
 * is what actually keeps every tile close to square. Shares `SectorHeatmapTreemap`'s tile chrome
 * (pale `--up`/`--down`-weak color scale, dark `#0e1319` text, hover-tooltip mechanics). A stock
 * tile has no drill-down (it's already the leaf, so no `selected`/`onSelect`). Tiles are
 * `role="button"` with roving-tabindex spatial arrow-nav (web/CLAUDE.md §7).
 */
export function StockHeatmapTreemap({
  rows,
  sizeBy = "turnover",
  maxIndustries,
  updatedAt,
}: {
  readonly rows: readonly StockHeatmapRow[];
  /** Which metric sizes each industry region/stock tile — mirrors `get_stock_heatmap`'s own
   * `size_by` input. */
  readonly sizeBy?: SectorHeatmapSizeBy;
  /** Caps how many industry regions are laid out (the biggest N by `sizeBy`) — the collapsed
   * inline view's readability cut. Omit to lay out every industry present in `rows`. */
  readonly maxIndustries?: number;
  /** `query.dataUpdatedAt` from the caller — every tile flashes once, at the SAME duration,
   * whenever fresh data lands (web/CLAUDE.md §4 non-trading motion), never scaled by
   * `pct_change`'s magnitude. */
  readonly updatedAt?: number;
}): ReactNode {
  const locale = useLocale();
  const t = useTranslations("market.cards.stockHeatmap");
  const containerRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(0);
  const [flashKey, setFlashKey] = useState(0);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);

  const tiles = useMemo(
    () => layoutNestedTiles(rows, sizeBy, maxIndustries),
    [rows, sizeBy, maxIndustries],
  );

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
        ?.querySelectorAll<HTMLButtonElement>("[data-stock-heatmap-tile]")
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
        const row = tile.item;
        const up = row.pct_change >= 0;
        const arrow = up ? "▲" : row.pct_change < 0 ? "▼" : "";
        const sign = up && row.pct_change > 0 ? "+" : "";
        const pctLabel = `${sign}${formatNumber(row.pct_change, locale, { decimals: 2 })}%`;
        const direction = up ? t("up") : row.pct_change < 0 ? t("down") : t("flat");
        const textSize = tileTextSize(tile);
        return (
          <div
            key={row.symbol}
            data-stock-heatmap-tile
            role="button"
            tabIndex={index === focusedIndex ? 0 : -1}
            aria-label={`${row.stock_name} ${row.symbol} ${direction} ${pctLabel}`}
            onFocus={() => setFocusedIndex(index)}
            onMouseEnter={() => setHoveredIndex(index)}
            onMouseLeave={() => setHoveredIndex((current) => (current === index ? null : current))}
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
                default:
                  break;
              }
            }}
            className={cn(
              "absolute flex cursor-default flex-col items-center justify-center gap-0.5 overflow-hidden p-1 text-center",
              "border-2 border-[var(--bg)] transition-[outline-color] focus-visible:outline focus-visible:outline-2 focus-visible:outline-[var(--accent)]",
              hoveredIndex === index
                ? "z-10 ring-2 ring-[var(--text)] ring-inset"
                : "overflow-hidden",
            )}
            style={{
              left: `${(tile.x / LAYOUT_WIDTH) * 100}%`,
              top: `${(tile.y / LAYOUT_HEIGHT) * 100}%`,
              width: `${(tile.width / LAYOUT_WIDTH) * 100}%`,
              height: `${(tile.height / LAYOUT_HEIGHT) * 100}%`,
              backgroundColor: heatColor(row.pct_change),
            }}
          >
            <span
              className={cn(
                "heat-pill max-w-full truncate font-semibold text-[var(--text)] leading-tight",
                textSize.name,
              )}
            >
              {row.stock_name}
            </span>
            <span
              className={cn(
                "heat-pill max-w-full truncate font-num text-[var(--text-muted)] tabular-nums leading-tight",
                textSize.symbol,
              )}
            >
              {row.symbol}
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
              animate={{ opacity: [0, 0.35, 0] }}
              transition={{ duration: 0.74, times: [0, 0.19, 1], ease: "easeOut" }}
            />
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
                <p className="font-semibold text-[12px] text-[var(--text)]">
                  {row.stock_name} {row.symbol}
                </p>
                <p className="mt-1 font-num text-[13px] text-[color:var(--text)] tabular-nums">
                  <span style={{ color: up ? "var(--up)" : "var(--down)" }}>
                    {arrow}
                    {pctLabel}
                  </span>
                </p>
                <p className="mt-1 text-[11px] text-[var(--text-muted)]">{row.industry}</p>
                <p className="mt-1 font-num text-[11px] text-[var(--text-muted)] tabular-nums">
                  {t("close")}: {formatNumber(row.close, locale, { decimals: 2 })}
                </p>
                {sizeMetricLabel(row, sizeBy, t, locale)}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
