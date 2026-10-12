"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { formatNumber } from "@/lib/format-number";
import { iconToSvgElement, sectorIconKeyForIndustry } from "@/lib/sector-rotation-icons";
import { colorForQuadrant, quadrantFor } from "./rrg-layout";

export interface RankPanelRow {
  readonly industryId: string;
  readonly industryName: string;
  readonly weeklyChangePct: number;
  readonly rsRatio: number;
  readonly rsMomentum: number;
}

/**
 * 類股排行 — icon+rank+name+% rows, ranked by |RS-Momentum| (the reference design's "依動能排序"
 * label) descending, so the sectors moving fastest right now sit at the top. Each icon's color is
 * the sector's CURRENT quadrant color (never a fixed per-sector hue), matching `SectorBadge`'s
 * exact color computation — the whole point of both reading `rrg-layout.ts`'s same
 * `quadrantFor`/`colorForQuadrant` functions rather than each re-deriving it.
 *
 * Row hover uses ONE delegated `onMouseOver`/`onMouseLeave` pair on the container (via
 * `data-industry-id` + `event.target.closest`) rather than a listener per row — this list
 * re-renders every integer week as playback advances, so a per-row listener would be
 * torn down/rebuilt every second at the default 4 weeks/sec rate; delegation is stable across
 * that churn.
 *
 * No bottom quadrant-color legend here — the 3D scene's own floating 領先/走弱/落後/轉強 labels
 * already carry that meaning as colored text (reference spec's explicit ask to drop the
 * redundant legend, which also frees vertical space on mobile).
 */
export function RankPanel({
  rows,
  hoveredSectorId,
  onHoverChange,
}: {
  readonly rows: readonly RankPanelRow[];
  readonly hoveredSectorId: string | null;
  readonly onHoverChange: (id: string | null) => void;
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  const locale = useLocale();

  const ranked = [...rows].sort(
    (a, b) => Math.abs(b.rsMomentum - 100) - Math.abs(a.rsMomentum - 100),
  );

  return (
    <div
      role="group"
      aria-label={t("title")}
      className="w-full rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)]/70 p-3 backdrop-blur-md"
      style={{
        boxShadow: "inset 0 1px 0 rgba(255,255,255,0.06)",
      }}
      onMouseOver={(event) => {
        const rowEl = (event.target as HTMLElement).closest<HTMLElement>("[data-industry-id]");
        if (rowEl?.dataset.industryId) onHoverChange(rowEl.dataset.industryId);
      }}
      onFocus={(event) => {
        const rowEl = (event.target as HTMLElement).closest<HTMLElement>("[data-industry-id]");
        if (rowEl?.dataset.industryId) onHoverChange(rowEl.dataset.industryId);
      }}
      onMouseLeave={() => onHoverChange(null)}
      onBlur={() => onHoverChange(null)}
    >
      <div className="mb-2 flex items-center justify-between px-1">
        <h3 className="font-semibold text-[13px] text-[var(--text)]">{t("title")}</h3>
        <span className="text-[11px] text-[var(--text-muted)]">{t("sortHint")}</span>
      </div>
      <ul className="flex flex-col gap-0.5">
        {ranked.map((row, index) => {
          const quadrant = quadrantFor(row.rsRatio, row.rsMomentum);
          const color = colorForQuadrant(quadrant);
          const icon = sectorIconKeyForIndustry(row.industryId);
          const up = row.weeklyChangePct >= 0;
          const isHovered = hoveredSectorId === row.industryId;
          return (
            <li
              key={row.industryId}
              data-industry-id={row.industryId}
              className={cn(
                "flex items-center gap-2 rounded-[var(--radius-input)] px-2 py-1.5 transition-colors",
                isHovered ? "bg-[var(--surface-2)]" : null,
              )}
            >
              <span className="w-5 shrink-0 font-num text-[11px] text-[var(--text-muted)] tabular-nums">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span style={{ color }} className="shrink-0">
                {iconToSvgElement(icon, 16)}
              </span>
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--text)]">
                {row.industryName}
              </span>
              <span
                className="font-num text-[12px] tabular-nums"
                style={{ color: up ? "var(--up)" : "var(--down)" }}
              >
                {up ? "▲" : "▼"}
                {formatNumber(Math.abs(row.weeklyChangePct), locale, { decimals: 1 })}%
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
