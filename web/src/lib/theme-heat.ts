/**
 * Concept-theme heat: the average daily change of a bounded sample of a theme's members, computed
 * from the quotes already on screen. It is a reference figure, never a fabricated one: a member
 * with no quote is left out, and a theme with no quoted member has no average at all.
 */

/** Members sampled per theme card, so a long theme does not fan out into dozens of quote calls. */
export const THEME_HEAT_SAMPLE = 12;

export type ThemeHeat = {
  /** Mean change in percent over the members that have a quote; null when none do. */
  readonly average: number | null;
  /** How many members the average is built from. */
  readonly quoted: number;
};

export function sampleMemberSymbols(
  members: readonly { readonly symbol: string }[],
  limit = THEME_HEAT_SAMPLE,
): string[] {
  return members.slice(0, Math.max(0, limit)).map((member) => member.symbol);
}

export function themeHeat(changes: readonly (number | null | undefined)[]): ThemeHeat {
  const known = changes.filter(
    (change): change is number => typeof change === "number" && Number.isFinite(change),
  );
  if (known.length === 0) {
    return { average: null, quoted: 0 };
  }
  const total = known.reduce((sum, change) => sum + change, 0);
  return { average: total / known.length, quoted: known.length };
}

/** Case-insensitive match on either name; a blank query matches every theme. */
export function matchesThemeQuery(
  theme: { readonly name_zh: string; readonly name_en: string },
  query: string,
): boolean {
  const needle = query.trim().toLowerCase();
  if (needle.length === 0) {
    return true;
  }
  return (
    theme.name_zh.toLowerCase().includes(needle) || theme.name_en.toLowerCase().includes(needle)
  );
}
