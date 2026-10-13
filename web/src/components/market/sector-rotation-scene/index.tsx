"use client";

import { useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { useRotationPlayback } from "@/hooks/use-rotation-playback";
import type { SectorRrgSeries } from "@/types/market/sectors";
import { QuadrantLegend, useQuadrantLabel } from "./quadrant-legend";
import { RankPanel, type RankPanelRow } from "./rank-panel";
import { RotationCanvas } from "./rotation-canvas";
import { RotationControls, type RotationView } from "./rotation-controls";
import {
  QUADRANT_ORDER,
  quadrantCounts,
  quadrantTransitions,
  type RotationQuadrant,
  rrgRange,
  sectorColor,
} from "./rrg-layout";
import { type SectorChip, SectorChips } from "./sector-chips";
import { SectorDetailCard } from "./sector-detail-card";
import { TransitionStrip } from "./transition-strip";

/** A sector's point at a whole week, clamped to its own series (a shorter series keeps its last
 * point rather than vanishing from the list). */
function pointAt(sector: SectorRrgSeries, week: number): SectorRrgSeries["points"][number] | null {
  return sector.points[Math.min(week, sector.points.length - 1)] ?? null;
}

/**
 * 板塊輪動 — the real-data Relative Rotation Graph in `SectorHeatmapPanel`'s "rotation" view.
 * It opens in the flat top-down view (every sector named, labels spread so they do not overlap)
 * with a 3D switch for the tilted, orbiting view. Around the scene: a quadrant legend with live
 * counts, the sectors that changed quadrant this week, sector chips + a detail card for the
 * selected one, and a transport bar (play/pause, scrub over the real week-ending dates).
 * `RankPanel` stays the same ranked list it always was.
 *
 * `sectors` must already be `data_available` (this component renders unconditionally once
 * mounted — the caller, `SectorHeatmapPanel`, owns the loading/empty/error states via
 * `QueryState`, same convention every other market card follows).
 */
export function SectorRotationScene({
  sectors,
  weekEndings,
}: {
  readonly sectors: readonly SectorRrgSeries[];
  readonly weekEndings: readonly string[];
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  const reducedMotion = useReducedMotion() ?? false;
  const playback = useRotationPlayback(weekEndings.length, { enabled: !reducedMotion });
  const [view, setView] = useState<RotationView>("flat");
  const [selectedSectorId, setSelectedSectorId] = useState<string | null>(null);

  const quadrantName = useQuadrantLabel();
  const quadrantLabels = Object.fromEntries(
    QUADRANT_ORDER.map((quadrant) => [quadrant, quadrantName(quadrant)]),
  ) as Record<RotationQuadrant, string>;
  const colors = useMemo(() => sectors.map((_, index) => sectorColor(index)), [sectors]);
  const range = useMemo(() => rrgRange(sectors), [sectors]);

  const week = Math.round(playback.currentWeek);
  const counts = useMemo(() => quadrantCounts(sectors, week), [sectors, week]);
  const transitions = useMemo(
    () => (week > 0 ? quadrantTransitions(sectors, week - 1, week) : []),
    [sectors, week],
  );

  const rankRows: RankPanelRow[] = [];
  const chips: SectorChip[] = [];
  for (const [index, sector] of sectors.entries()) {
    const point = pointAt(sector, week);
    if (!point) continue;
    rankRows.push({
      industryId: sector.industry_id,
      industryName: sector.industry_name,
      weeklyChangePct: point.weekly_change_pct,
      rsRatio: point.rs_ratio,
      rsMomentum: point.rs_momentum,
    });
    chips.push({
      industryId: sector.industry_id,
      industryName: sector.industry_name,
      color: colors[index] ?? sectorColor(index),
    });
  }
  const selectedRow = rankRows.find((row) => row.industryId === selectedSectorId);
  const selectedSector = sectors.find((sector) => sector.industry_id === selectedSectorId);
  const detail =
    selectedRow && selectedSector ? { ...selectedRow, weight: selectedSector.weight } : null;

  // In a narrow card everything stacks (scene, detail, controls + chips, then the ranking list)
  // so nothing sits on top of the scene; once the card is wide enough the panels overlay it as in
  // the design. It is the card's width that decides, not the screen's: the sidebar and the
  // watchlist take a lot of a laptop screen.
  return (
    <div className="@container w-full">
      <div className="relative @min-[60rem]:block flex @min-[60rem]:h-[clamp(540px,80vh,840px)] w-full flex-col gap-3 overflow-hidden rounded-[var(--radius-card)] bg-[radial-gradient(120%_90%_at_50%_35%,#15183a_0%,#080a18_70%)] @min-[60rem]:pb-0 pb-3">
        <div className="@min-[60rem]:absolute relative @min-[60rem]:inset-x-0 @min-[60rem]:top-0 @min-[60rem]:right-[19.5rem] @min-[60rem]:bottom-36 @min-[60rem]:left-56 order-1 @min-[60rem]:h-auto h-[21rem]">
          <RotationCanvas
            sectors={sectors}
            colors={colors}
            range={range}
            quadrantLabels={quadrantLabels}
            currentWeek={playback.currentWeek}
            hoveredSectorId={playback.hoveredSectorId}
            selectedSectorId={selectedSectorId}
            view={view}
            reducedMotion={reducedMotion}
            onHoverChange={playback.setHoveredSectorId}
            onSelect={setSelectedSectorId}
          />
          <div className="pointer-events-none absolute top-2 left-3 @min-[60rem]:hidden">
            <QuadrantLegend counts={counts} />
          </div>
        </div>

        <div className="pointer-events-none absolute top-3 left-3 @min-[60rem]:block hidden">
          <QuadrantLegend counts={counts} />
        </div>
        <div className="pointer-events-auto @min-[60rem]:absolute @min-[60rem]:top-[7.25rem] @min-[60rem]:left-3 order-2 flex @min-[60rem]:w-56 flex-col gap-3 @min-[60rem]:px-0 px-3">
          <SectorDetailCard detail={detail} onClear={() => setSelectedSectorId(null)} />
          <TransitionStrip transitions={transitions} />
        </div>

        <div className="pointer-events-auto @min-[60rem]:absolute @min-[60rem]:top-5 @min-[60rem]:right-5 order-4 @min-[60rem]:w-72 @min-[60rem]:px-0 px-3">
          <RankPanel
            rows={rankRows}
            hoveredSectorId={playback.hoveredSectorId}
            onHoverChange={playback.setHoveredSectorId}
          />
        </div>

        <div className="pointer-events-none @min-[60rem]:absolute @min-[60rem]:inset-x-3 @min-[60rem]:bottom-2 order-3 flex flex-col gap-2 @min-[60rem]:px-0 px-3">
          <SectorChips
            chips={chips}
            selectedSectorId={selectedSectorId}
            hoveredSectorId={playback.hoveredSectorId}
            onSelect={setSelectedSectorId}
            onHoverChange={playback.setHoveredSectorId}
          />
          <RotationControls
            weekEndings={weekEndings}
            currentWeek={playback.currentWeek}
            isPlaying={playback.isPlaying}
            canPlay={!reducedMotion}
            view={view}
            onTogglePlaying={playback.togglePlaying}
            onSeek={playback.seek}
            onViewChange={setView}
          />
          <p className="text-[11px] text-[var(--text-muted)]">{t("disclaimer")}</p>
        </div>
      </div>
    </div>
  );
}
