/**
 * The motion scale for `motion/react`, mirroring the CSS tokens (`--dur-*`, `--ease-*`) so a JS
 * animation and a CSS transition feel identical. Restrained by design: feedback is quick, an
 * entrance is a short fade-and-rise, and nothing scales with a price move (web/OVERVIEW.md §4).
 * Every consumer must also honour reduced motion (`useReducedMotion`) - use `entrance()`, which
 * already does.
 */
import type { Variants } from "motion/react";

/** Seconds, matching `--dur-fast/base/slow`. */
export const DURATION = { fast: 0.12, base: 0.2, slow: 0.32 } as const;

/** Matches `--ease-out` / `--ease-in-out`. */
export const EASE = {
  out: [0.22, 1, 0.36, 1],
  inOut: [0.65, 0, 0.35, 1],
} as const;

/** Delay between siblings in a staggered reveal - short enough that a list never feels slow. */
export const STAGGER = 0.035;

/** A fade that rises 6px. Under reduced motion it is an instant, opacity-only change. */
export function entrance(reduceMotion: boolean | null): Variants {
  if (reduceMotion) {
    return { hidden: { opacity: 1 }, show: { opacity: 1 } };
  }
  return {
    hidden: { opacity: 0, y: 6 },
    show: { opacity: 1, y: 0, transition: { duration: DURATION.base, ease: EASE.out } },
  };
}

/** Parent variants that stagger their children's `entrance()`. */
export function staggerChildren(reduceMotion: boolean | null): Variants {
  return {
    hidden: {},
    show: { transition: { staggerChildren: reduceMotion ? 0 : STAGGER } },
  };
}
