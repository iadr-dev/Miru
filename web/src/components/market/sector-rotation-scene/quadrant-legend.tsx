"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { colorForQuadrant, QUADRANT_ORDER, type RotationQuadrant } from "./rrg-layout";

const LABEL_KEY: Record<RotationQuadrant, string> = {
  leading: "quadrantLeading",
  weakening: "quadrantWeakening",
  lagging: "quadrantLagging",
  improving: "quadrantImproving",
};

/** Translated name of a quadrant, shared by the legend, the detail card and the transitions. */
export function useQuadrantLabel(): (quadrant: RotationQuadrant) => string {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  return (quadrant) => t(LABEL_KEY[quadrant]);
}

/** A fixed screen-space legend (plain HTML over the canvas, so it stays crisp at any camera
 * angle) listing the four quadrants with how many sectors sit in each this week. The colour is
 * paired with the quadrant's name, never colour alone. */
export function QuadrantLegend({
  counts,
}: {
  readonly counts: Record<RotationQuadrant, number>;
}): ReactNode {
  const label = useQuadrantLabel();
  return (
    <ul className="pointer-events-none flex flex-col gap-1">
      {QUADRANT_ORDER.map((quadrant) => (
        <li
          key={quadrant}
          className="flex items-center gap-1.5 font-semibold text-[12px] tracking-wide"
          style={{ color: colorForQuadrant(quadrant) }}
        >
          <span
            aria-hidden
            className="size-2 rounded-full"
            style={{ background: colorForQuadrant(quadrant) }}
          />
          {label(quadrant)}
          <span className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
            {counts[quadrant]}
          </span>
        </li>
      ))}
    </ul>
  );
}
