import type { EtfListRow, EtfSortKey, SortDirection } from "@/types/market/etf";

// desc for every numeric column (largest-first, matching the reference app's own default
// ordering); asc for name (alphabetical is the natural default there, not "largest name").
export const DEFAULT_SORT_DIRECTION: Record<EtfSortKey, SortDirection> = {
  name: "asc",
  close: "desc",
  change_pct: "desc",
  turnover: "desc",
  return_1w_pct: "desc",
  distribution_yield_ttm_pct: "desc",
  aum: "desc",
  expense_ratio_pct_approx: "desc",
};

type SortValue = string | number | null;

function sortValueFor(row: EtfListRow, key: EtfSortKey): SortValue {
  switch (key) {
    case "name":
      return row.name;
    case "close":
      return row.close;
    case "change_pct":
      return row.change_pct;
    case "turnover":
      return row.turnover;
    case "return_1w_pct":
      return row.return_1w_pct;
    case "distribution_yield_ttm_pct":
      return row.distribution_yield_ttm_pct;
    case "aum":
      return row.aum;
    case "expense_ratio_pct_approx":
      return row.expense_ratio_pct_approx;
    default:
      return null;
  }
}

/** Nulls always sort last regardless of direction — a missing value is never "smallest"/
 * "largest", it's simply not comparable, so it never competes for the top of either order.
 *
 * Kept in its own module (no React/next-intl imports) so `etf-list-table.test.ts` can exercise
 * this pure sort logic under Vitest's plain `node` test environment — `etf-list-table.tsx`
 * itself transitively imports `next-intl`'s navigation (via `SymbolLink`), which requires a real
 * Next.js runtime and breaks that test's import graph if this logic stays co-located there. */
export function compareRows(
  a: EtfListRow,
  b: EtfListRow,
  key: EtfSortKey,
  direction: SortDirection,
): number {
  const av = sortValueFor(a, key);
  const bv = sortValueFor(b, key);
  if (av === null && bv === null) return 0;
  if (av === null) return 1;
  if (bv === null) return -1;
  const sign = direction === "asc" ? 1 : -1;
  if (typeof av === "string" || typeof bv === "string") {
    return sign * String(av).localeCompare(String(bv), "zh-TW");
  }
  return sign * (av - bv);
}
