/** Tiny classnames joiner — filters falsy values. Kept dependency-free (no clsx/tailwind-merge
 * needed at this scale); we author non-conflicting class lists. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
