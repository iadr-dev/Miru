"use client";

import { ArrowRightIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useQuadrantLabel } from "./quadrant-legend";
import { colorForQuadrant, type QuadrantTransition, shortSectorName } from "./rrg-layout";

const MAX_SHOWN = 4;

/** The sectors that crossed into another quadrant in the week on screen. Derived from the week
 * alone (no timers, no stored state), so scrubbing back and forth shows the same notes for the
 * same week. Not an aria-live region: it changes on every playback step. */
export function TransitionStrip({
  transitions,
}: {
  readonly transitions: readonly QuadrantTransition[];
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  const label = useQuadrantLabel();
  if (transitions.length === 0) return null;
  return (
    <section
      aria-label={t("transitionsTitle")}
      className="pointer-events-none flex flex-col gap-1 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]/70 px-3 py-2 backdrop-blur-md"
    >
      <h4 className="text-[11px] text-[var(--text-muted)]">{t("transitionsTitle")}</h4>
      <ul className="flex flex-col gap-0.5">
        {transitions.slice(0, MAX_SHOWN).map((item) => (
          <li
            key={item.industryId}
            className="flex items-center gap-1.5 text-[12px] text-[var(--text)]"
          >
            <span className="min-w-0 truncate">{shortSectorName(item.industryName)}</span>
            <span style={{ color: colorForQuadrant(item.from) }}>{label(item.from)}</span>
            <ArrowRightIcon size={12} aria-hidden className="shrink-0 text-[var(--text-muted)]" />
            <span className="font-semibold" style={{ color: colorForQuadrant(item.to) }}>
              {label(item.to)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
