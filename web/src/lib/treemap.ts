/**
 * Generic squarified-treemap layout (Bruls, Huizing, van Wijk 1999) — the same algorithm
 * `sector-heatmap-treemap.tsx`'s own `squarify` implements for `SectorHeatmapRow`, extracted here
 * so a second treemap (`stock-heatmap-treemap.tsx`) doesn't duplicate the ~50-line layout math for
 * a different row type. Operates on a generic `{ id, size }` item — callers map their own rows in
 * and their own tiles back out.
 */

export interface TreemapTile<T> {
  item: T;
  x: number;
  y: number;
  width: number;
  height: number;
}

interface SizedItem<T> {
  item: T;
  size: number;
}

/** The worst (largest) width:height ratio any tile in `row` would get if laid out at `length`
 * along the shorter side of the remaining box — the classic squarified-treemap "worst aspect
 * ratio" measure used to decide how many items belong in the current row before starting a new
 * one. */
function worstRatio<T>(row: readonly SizedItem<T>[], length: number): number {
  const rowTotal = row.reduce((sum, entry) => sum + entry.size, 0);
  const rowThickness = rowTotal / length;
  let worst = 0;
  for (const entry of row) {
    const itemLength = entry.size / rowThickness;
    const ratio = Math.max(itemLength / rowThickness, rowThickness / itemLength);
    if (ratio > worst) worst = ratio;
  }
  return worst;
}

function layoutRow<T>(
  row: readonly SizedItem<T>[],
  x: number,
  y: number,
  w: number,
  h: number,
  tiles: TreemapTile<T>[],
): void {
  const rowTotal = row.reduce((sum, entry) => sum + entry.size, 0);
  const isWide = w >= h;
  const thickness = isWide ? rowTotal / h : rowTotal / w;
  let offset = isWide ? y : x;
  for (const entry of row) {
    const extent = entry.size / (thickness || 1);
    if (isWide) {
      tiles.push({ item: entry.item, x, y: offset, width: thickness, height: extent });
      offset += extent;
    } else {
      tiles.push({ item: entry.item, x: offset, y, width: extent, height: thickness });
      offset += extent;
    }
  }
}

/** A squarified-treemap layout over `items`, each already reduced to a positive `size` by the
 * caller (via `sizeOf`). Grows the current row one item at a time along the box's shorter side,
 * stopping the instant the worst aspect ratio in that row would get worse by adding another item
 * — the standard squarify recursion, not a fixed-threshold heuristic (a naive threshold
 * degenerates into one giant stacked-bar row for a power-law-shaped size distribution, which real
 * turnover/market-value/volume data is). */
export function squarifyGeneric<T>(
  items: readonly T[],
  sizeOf: (item: T) => number,
  width: number,
  height: number,
): TreemapTile<T>[] {
  const sized: SizedItem<T>[] = items
    .map((item) => ({ item, size: Math.max(sizeOf(item), 0.0001) }))
    .sort((a, b) => b.size - a.size);
  const total = sized.reduce((sum, entry) => sum + entry.size, 0);
  if (total <= 0 || sized.length === 0) return [];

  const scale = (width * height) / total;
  const scaled = sized.map((entry) => ({ ...entry, size: entry.size * scale }));
  const tiles: TreemapTile<T>[] = [];

  let remaining = scaled;
  let x = 0;
  let y = 0;
  let w = width;
  let h = height;
  while (remaining.length > 0) {
    const shortSide = Math.min(w, h);
    let row: SizedItem<T>[] = [remaining[0] as SizedItem<T>];
    let rest = remaining.slice(1);
    let bestRatio = worstRatio(row, shortSide);
    while (rest.length > 0) {
      const candidateRow = [...row, rest[0] as SizedItem<T>];
      const candidateRatio = worstRatio(candidateRow, shortSide);
      if (candidateRatio > bestRatio) break;
      row = candidateRow;
      rest = rest.slice(1);
      bestRatio = candidateRatio;
    }
    remaining = rest;
    const rowTotal = row.reduce((sum, entry) => sum + entry.size, 0);
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
