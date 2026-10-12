// Categorical only — never `--up`/`--down` (those are reserved for signed price/%/score
// deltas, web/OVERVIEW.md §4). No dedicated per-category hue token set exists in tokens.css beyond
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

/** Where a label goes when its hashed color is already taken in the same chart. Ordered by how far
 * each hue sits from the others (orange, purple, teal, pink, blue, lime, …) rather than by token
 * number — the next token after `--chart-6` (gray-blue) is `--chart-7`, a sky blue that looks the
 * same at line width. `tokens.css` defines `--chart-1..10`. */
const COLLISION_ORDER = [
  "var(--chart-3)",
  "var(--chart-2)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-1)",
  "var(--chart-9)",
  "var(--chart-8)",
  "var(--chart-10)",
  "var(--chart-7)",
  "var(--chart-6)",
] as const;

/** One color per label, guaranteed DISTINCT within a chart. `hashToChartToken` alone maps a label
 * into just six tokens, so two series in the same chart could land on the same one — a comparison
 * of 0050 and 00878 drew both lines in the same gray-blue. Each label keeps its stable hashed color
 * when that is still free (so a label looks the same chart to chart whenever possible) and otherwise
 * takes the most contrasting unused token. Deterministic for a given ordered label list; beyond ten
 * labels the colors repeat (callers already cap series at ten). */
export function distinctChartColors(labels: readonly string[]): Record<string, string> {
  const colors: Record<string, string> = {};
  const used = new Set<string>();
  for (const label of labels) {
    if (label in colors) {
      continue;
    }
    const preferred = hashToChartToken(label);
    const chosen = used.has(preferred)
      ? (COLLISION_ORDER.find((candidate) => !used.has(candidate)) ?? preferred)
      : preferred;
    colors[label] = chosen;
    used.add(chosen);
  }
  return colors;
}
