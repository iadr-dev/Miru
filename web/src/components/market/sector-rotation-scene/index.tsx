"use client";

import { useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useRotationPlayback } from "@/hooks/use-rotation-playback";
import type { SectorRrgSeries } from "@/types/market/sectors";
import { RankPanel, type RankPanelRow } from "./rank-panel";
import { RotationCanvas } from "./rotation-canvas";
import { colorForQuadrant, type RotationQuadrant } from "./rrg-layout";

// Taller than the old fixed 640px so the scene reads large; scales with the viewport, capped.
const SCENE_HEIGHT = "clamp(420px, 72vh, 760px)";

const QUADRANT_LEGEND_ORDER: readonly RotationQuadrant[] = [
  "leading",
  "weakening",
  "lagging",
  "improving",
];

/** A fixed screen-space legend (top-left, the one corner `RankPanel`/the disclaimer don't
 * already occupy) — unlike the 3D scene's own quadrant floor tiles/ring (which rotate and
 * foreshorten with the camera, confirmed unreadable at most angles via a live screenshot), this
 * is plain absolutely-positioned HTML over the canvas, so all four 領先/走弱/落後/轉強 labels stay
 * crisp and equally legible regardless of camera angle. */
function QuadrantLegend(): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  const labelKey: Record<RotationQuadrant, string> = {
    leading: "quadrantLeading",
    weakening: "quadrantWeakening",
    lagging: "quadrantLagging",
    improving: "quadrantImproving",
  };
  return (
    <div className="pointer-events-none absolute top-3 left-3 flex flex-col gap-1">
      {QUADRANT_LEGEND_ORDER.map((quadrant) => (
        <span
          key={quadrant}
          className="font-semibold text-[12px] tracking-wide"
          style={{ color: colorForQuadrant(quadrant) }}
        >
          {t(labelKey[quadrant])}
        </span>
      ))}
    </div>
  );
}

/**
 * 板塊輪動 — the real-data 3D Relative Rotation Graph, replacing the old bar-chart/particle-
 * sphere toggle in `SectorHeatmapPanel`'s "rotation" view. Composes `useRotationPlayback` (the
 * one playback clock, always autoplaying here — no user-facing transport controls or title
 * block, per explicit product direction: this view narrates itself continuously with just the
 * 3D scene + `RankPanel` overlay, no chrome around it) with `RotationCanvas` (the 3D scene).
 *
 * `sectors` must already be `data_available` (this component renders unconditionally once
 * mounted — the caller, `SectorHeatmapPanel`, is responsible for the loading/empty/error states
 * via `QueryState`, same convention every other market card follows).
 */
export function SectorRotationScene({
  sectors,
  weekCount,
}: {
  readonly sectors: readonly SectorRrgSeries[];
  readonly weekCount: number;
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  const reducedMotion = useReducedMotion() ?? false;
  const playback = useRotationPlayback(weekCount, { enabled: !reducedMotion });

  const floorWeek = Math.round(playback.currentWeek);
  const rankRows: RankPanelRow[] = sectors
    .map((sector) => {
      const point = sector.points[Math.min(floorWeek, sector.points.length - 1)];
      if (!point) return null;
      return {
        industryId: sector.industry_id,
        industryName: sector.industry_name,
        weeklyChangePct: point.weekly_change_pct,
        rsRatio: point.rs_ratio,
        rsMomentum: point.rs_momentum,
      };
    })
    .filter((row): row is RankPanelRow => row !== null);

  return (
    <div
      className="relative w-full overflow-hidden rounded-[var(--radius-card)] bg-[#05070b]"
      style={{ height: SCENE_HEIGHT }}
    >
      <div className="absolute inset-0">
        <RotationCanvas
          sectors={sectors}
          currentWeek={playback.currentWeek}
          hoveredSectorId={playback.hoveredSectorId}
          reducedMotion={reducedMotion}
        />
      </div>

      <QuadrantLegend />

      <div className="pointer-events-auto absolute top-5 right-5 w-64 sm:w-72">
        <RankPanel
          rows={rankRows}
          hoveredSectorId={playback.hoveredSectorId}
          onHoverChange={playback.setHoveredSectorId}
        />
      </div>

      <p className="pointer-events-none absolute bottom-3 left-5 text-[11px] text-[var(--text-muted)]">
        {t("disclaimer")}
      </p>
    </div>
  );
}
