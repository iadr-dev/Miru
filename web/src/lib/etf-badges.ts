/** Shared ETF fund-structure badge constants — 原型/槓桿型/反向型 (from `symbol_type`) and
 * 主動式 (from `is_active_etf`), reused verbatim by `etf-list-table.tsx` (the market ETF-tab
 * list) and `asset-header.tsx` (the ETF detail page's own header) so the same fund never reads
 * as a different color/label depending on which page it's viewed from. Categorical tokens only
 * (`--chart-1..3`), never `--up`/`--down` — these are fund-structure facts, not price/%/score
 * signals, per web/OVERVIEW.md §4.
 */
export const SYMBOL_TYPE_BADGE: Record<string, { labelKey: string; color: string }> = {
  etf_leveraged: { labelKey: "leveraged", color: "var(--chart-1)" },
  etf_inverse: { labelKey: "inverse", color: "var(--chart-2)" },
};

export const ACTIVE_ETF_BADGE_COLOR = "var(--chart-3)";
