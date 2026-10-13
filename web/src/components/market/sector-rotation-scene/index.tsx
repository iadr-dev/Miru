"use client";

import { useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { useRotationPlayback } from "@/hooks/use-rotation-playback";
import type { SectorRrgSeries } from "@/types/market/sectors";
import { QuadrantLegend } from "./quadrant-legend";
import { RankPanel, type RankPanelRow } from "./rank-panel";
import { RotationCanvas } from "./rotation-canvas";
import { RotationControls, type RotationView } from "./rotation-controls";
import { quadrantCounts, quadrantFor, quadrantTransitions } from "./rrg-layout";
import { type SectorChip, SectorChips } from "./sector-chips";
import { SectorDetailCard } from "./sector-detail-card";
import { TransitionStrip } from "./transition-strip";

// Tall enough for the scene plus the chip row and transport bar stacked over its bottom edge.
const SCENE_HEIGHT = "clamp(540px, 80vh, 840px)";

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

  const week = Math.round(playback.currentWeek);
  const counts = useMemo(() => quadrantCounts(sectors, week), [sectors, week]);
  const transitions = useMemo(
    () => (week > 0 ? quadrantTransitions(sectors, week - 1, week) : []),
    [sectors, week],
  );

  const rankRows: RankPanelRow[] = [];
  const chips: SectorChip[] = [];
  for (const sector of sectors) {
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
      quadrant: quadrantFor(point.rs_ratio, point.rs_momentum),
    });
  }
  const selectedRow = rankRows.find((row) => row.industryId === selectedSectorId);
  const selectedSector = sectors.find((sector) => sector.industry_id === selectedSectorId);
  const detail =
    selectedRow && selectedSector ? { ...selectedRow, weight: selectedSector.weight } : null;

  return (
    <div
      className="relative w-full overflow-hidden rounded-[var(--radius-card)] bg-[#05070b]"
      style={{ height: SCENE_HEIGHT }}
    >
      <div className="absolute inset-x-0 top-0 bottom-36 sm:right-[19.5rem] sm:left-56">
        <RotationCanvas
          sectors={sectors}
          currentWeek={playback.currentWeek}
          hoveredSectorId={playback.hoveredSectorId}
          selectedSectorId={selectedSectorId}
          view={view}
          reducedMotion={reducedMotion}
          onHoverChange={playback.setHoveredSectorId}
          onSelect={setSelectedSectorId}
        />
      </div>

      <div className="pointer-events-none absolute top-3 left-3 flex w-56 flex-col gap-3">
        <QuadrantLegend counts={counts} />
        <div className="pointer-events-auto hidden flex-col gap-3 sm:flex">
          <SectorDetailCard detail={detail} onClear={() => setSelectedSectorId(null)} />
          <TransitionStrip transitions={transitions} />
        </div>
      </div>

      <div className="pointer-events-auto absolute top-5 right-5 w-64 sm:w-72">
        <RankPanel
          rows={rankRows}
          hoveredSectorId={playback.hoveredSectorId}
          onHoverChange={playback.setHoveredSectorId}
        />
      </div>

      <div className="pointer-events-none absolute inset-x-3 bottom-2 flex flex-col gap-2">
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
  );
}
