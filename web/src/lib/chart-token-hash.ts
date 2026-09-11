// Categorical only — never `--up`/`--down` (those are reserved for signed price/%/score
// deltas, web/CLAUDE.md §4). No dedicated per-category hue token set exists in tokens.css beyond
// the shared `--chart-1..6` set, so this hashes a label into that set deterministically: the
// same label always renders the same color. Shared by `SectorChip` and the 概念股 theme chips —
// both are "pick a stable color for an arbitrary category string" the same way.
const CHART_TOKENS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

export function hashToChartToken(label: string): string {
  let hash = 0;
  for (let i = 0; i < label.length; i++) {
    hash = (hash * 31 + label.charCodeAt(i)) >>> 0;
  }
  return CHART_TOKENS[hash % CHART_TOKENS.length] ?? CHART_TOKENS[0];
}
