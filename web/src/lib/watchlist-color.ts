import type { CSSProperties } from "react";
import type { WatchlistColor } from "@/types/watchlist";

/** A group name's text colour: the chosen chart token pulled toward the theme's text colour, so it
 * stays readable on any surface in light and dark (the raw chart tokens only promise the 3:1
 * graphical-element contrast, not the 4.5:1 body text needs). */
export function groupTextColor(color: WatchlistColor): string {
  return `color-mix(in srgb, var(--${color}) 70%, var(--text))`;
}

/** Inline style for wherever a group's name is listed; nothing for an uncoloured group. */
export function groupNameStyle(group: {
  color?: WatchlistColor | null | undefined;
}): CSSProperties | undefined {
  return group.color ? { color: groupTextColor(group.color) } : undefined;
}
