import type { BatchAlertItem } from "@/lib/api/settings";
import type { Market } from "@/types/settings";

/** Pure logic behind the per-stock ("逐檔設定") alert sheet — no React, no i18n. One price level
 * cannot mean the same thing for every stock in a group (2,475 vs 185), so each row carries its own
 * comparator and level; a row with no level is simply skipped. */

export type RowComparator = "above" | "below";

export type PerStockRow = {
  symbol: string;
  market: Market;
  /** The latest close, shown as a reference and used by the quick-fill buttons; null if unknown. */
  close: number | null;
  comparator: RowComparator;
  /** Raw text of the level input ("" = this stock is skipped). */
  level: string;
};

/** The level `percent` away from `close` on the comparator's side, to two decimals: "above +5%" is
 * 5% over the close, "below -5%" is 5% under it. Empty when there is no close to measure from. */
export function quickFillLevel(
  close: number | null,
  comparator: RowComparator,
  percent: number,
): string {
  if (close === null || !Number.isFinite(close) || close <= 0) {
    return "";
  }
  const factor = comparator === "above" ? 1 + percent / 100 : 1 - percent / 100;
  const level = Math.round(close * factor * 100) / 100;
  return level > 0 ? String(level) : "";
}

/** Fill every row that has no level yet (rows the user already typed are left alone). */
export function fillBlankRows(
  rows: readonly PerStockRow[],
  comparator: RowComparator,
  percent: number,
): PerStockRow[] {
  return rows.map((row) =>
    row.level.trim() === ""
      ? { ...row, comparator, level: quickFillLevel(row.close, comparator, percent) }
      : row,
  );
}

function parseLevel(raw: string): number | null {
  const text = raw.trim();
  if (text === "") {
    return null;
  }
  const n = Number(text);
  return Number.isFinite(n) && n > 0 ? n : null;
}

export type BatchCheck =
  | { ok: true; items: BatchAlertItem[]; skipped: number }
  | { ok: false; reason: "nothing" | "invalid"; badSymbols: string[] };

/** Turn the rows into the API payload: rows with a blank level are skipped, a non-positive or
 * non-numeric level is an error naming the stock, and at least one row must have a level. */
export function buildBatch(rows: readonly PerStockRow[]): BatchCheck {
  const items: BatchAlertItem[] = [];
  const bad: string[] = [];
  let skipped = 0;
  for (const row of rows) {
    if (row.level.trim() === "") {
      skipped += 1;
      continue;
    }
    const value = parseLevel(row.level);
    if (value === null) {
      bad.push(row.symbol);
      continue;
    }
    items.push({
      symbol: row.symbol,
      market: row.market,
      condition: { kind: "close_price", comparator: row.comparator, value },
    });
  }
  if (bad.length > 0) {
    return { ok: false, reason: "invalid", badSymbols: bad };
  }
  if (items.length === 0) {
    return { ok: false, reason: "nothing", badSymbols: [] };
  }
  return { ok: true, items, skipped };
}
