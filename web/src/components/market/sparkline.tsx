"use client";

import type { ReactNode } from "react";

const VIEW_WIDTH = 100;
const VIEW_HEIGHT = 26;

/**
 * A minimal inline price-history line — no new chart-library dependency for a decorative,
 * few-point trend indicator (`web/OVERVIEW.md`'s `lightweight-charts`/`visx`/`recharts` stack is
 * for real interactive charts; this is a tiny presentational SVG, same class as the mockup's own
 * `.lb-spark` polyline). Renders nothing (never a flat fabricated line) when fewer than 2 points
 * exist. Purely decorative: the real values are announced via the row's own `aria-label`
 * elsewhere, so this stays `aria-hidden`.
 */
export function Sparkline({
  values,
  color,
}: {
  values: readonly number[];
  color: string;
}): ReactNode {
  if (values.length < 2) {
    return null;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const points = values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * VIEW_WIDTH;
      const y = VIEW_HEIGHT - ((value - min) / range) * VIEW_HEIGHT;
      return `${x},${y}`;
    })
    .join(" ");

  return (
    <svg
      viewBox={`0 0 ${VIEW_WIDTH} ${VIEW_HEIGHT}`}
      preserveAspectRatio="none"
      className="block h-full w-full"
      aria-hidden
      role="presentation"
    >
      <polyline points={points} fill="none" stroke={color} strokeWidth={1.6} />
    </svg>
  );
}
