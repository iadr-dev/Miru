"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { type ReactNode, useMemo, useRef, useState } from "react";
import { sectorIconKeyForIndustry } from "@/lib/sector-rotation-icons";
import type { SectorRrgSeries } from "@/types/market";
import { CometTail } from "./comet-tail";
import { CompassRing } from "./compass-ring";
import { QuadrantFloor } from "./quadrant-floor";
import { colorForQuadrant, lerpPoint, quadrantFor, rrgToWorld } from "./rrg-layout";
import { SectorBadge } from "./sector-badge";
import { SectorLabel } from "./sector-label";
import { Starfield } from "./starfield";

const INTRO_DURATION_S = 1.15;
const BADGE_HEIGHT = 0.35;
const LABEL_HEIGHT = 0.95;
// Far enough back that the whole compass ring (radius 8.8) stays in frame as the camera orbits:
// its near edge sits ~19 deg below the view centre, inside the 45 deg fov's 22.5 deg half-angle with
// room for the badges. The old [0, 11, 12] put that edge ~31 deg down, cut off at the bottom.
const CAMERA_START: readonly [number, number, number] = [0, 16, 17.4];
const AUTO_ROTATE_RADIANS_PER_SECOND = 0.05;

/** Eases 0→1 over `INTRO_DURATION_S` seconds via `useFrame` — the single shared "boot the
 * console" progress every floor/ring/badge in the scene multiplies its own opacity/scale by
 * (spec's own "one intro moment, everything else stays calm" instruction). Skips straight to 1
 * when `reducedMotion` is set, per web/OVERVIEW.md §7's reduced-motion tiers. */
function useIntroProgress(reducedMotion: boolean): number {
  const [progress, setProgress] = useState(reducedMotion ? 1 : 0);
  const elapsedRef = useRef(0);

  useFrame((_state, delta) => {
    if (reducedMotion || progress >= 1) return;
    elapsedRef.current += delta;
    const t = Math.min(elapsedRef.current / INTRO_DURATION_S, 1);
    // ease-out-cubic
    setProgress(1 - (1 - t) ** 3);
  });

  return progress;
}

function AutoRotateController({
  userInteracting,
  reducedMotion,
}: {
  readonly userInteracting: boolean;
  readonly reducedMotion: boolean;
}): ReactNode {
  useFrame(({ camera }, delta) => {
    if (userInteracting || reducedMotion) return;
    const angle = AUTO_ROTATE_RADIANS_PER_SECOND * delta;
    const x = camera.position.x * Math.cos(angle) - camera.position.z * Math.sin(angle);
    const z = camera.position.x * Math.sin(angle) + camera.position.z * Math.cos(angle);
    camera.position.x = x;
    camera.position.z = z;
    camera.lookAt(0, 0, 0);
  });
  return null;
}

function SceneContents({
  sectors,
  currentWeek,
  hoveredSectorId,
  reducedMotion,
  userInteracting,
}: {
  readonly sectors: readonly SectorRrgSeries[];
  readonly currentWeek: number;
  readonly hoveredSectorId: string | null;
  readonly reducedMotion: boolean;
  readonly userInteracting: boolean;
}): ReactNode {
  const introProgress = useIntroProgress(reducedMotion);
  const weekCount = sectors[0]?.points.length ?? 0;
  const playbackFraction = weekCount > 1 ? currentWeek / (weekCount - 1) : 0;

  const badgeData = useMemo(() => {
    const floorWeek = Math.floor(currentWeek);
    const frac = currentWeek - floorWeek;
    return sectors.map((sector) => {
      const a = sector.points[floorWeek];
      const b = sector.points[Math.min(floorWeek + 1, sector.points.length - 1)];
      if (!a || !b) return null;
      const interpolated = lerpPoint(a, b, frac);
      const { x, z } = rrgToWorld(interpolated.rs_ratio, interpolated.rs_momentum);
      const quadrant = quadrantFor(interpolated.rs_ratio, interpolated.rs_momentum);
      return {
        industryId: sector.industry_id,
        industryName: sector.industry_name,
        weight: sector.weight,
        position: [x, BADGE_HEIGHT, z] as const,
        labelPosition: [x, LABEL_HEIGHT, z] as const,
        color: colorForQuadrant(quadrant),
      };
    });
  }, [sectors, currentWeek]);

  return (
    <>
      <ambientLight intensity={0.6} />
      <Starfield reducedMotion={reducedMotion} />
      <QuadrantFloor introProgress={introProgress} />
      <CompassRing
        introProgress={introProgress}
        playbackFraction={playbackFraction}
        reducedMotion={reducedMotion}
      />
      {sectors.map((sector) => (
        <CometTail
          key={sector.industry_id}
          points={sector.points}
          throughWeekIndex={Math.floor(currentWeek)}
          color={colorForQuadrant(
            quadrantFor(
              sector.points[Math.floor(currentWeek)]?.rs_ratio ?? 100,
              sector.points[Math.floor(currentWeek)]?.rs_momentum ?? 100,
            ),
          )}
          introProgress={introProgress}
        />
      ))}
      {badgeData.map((badge) => {
        if (!badge) return null;
        const isHovered = hoveredSectorId === badge.industryId;
        return (
          <group key={badge.industryId}>
            <SectorBadge
              position={badge.position}
              icon={sectorIconKeyForIndustry(badge.industryId)}
              color={badge.color}
              weight={badge.weight}
              hovered={isHovered}
              introProgress={introProgress}
            />
            {/* Sector name labels are hover-only, not always-on — with 10 badges frequently
             * clustering close together spatially, every name floating at once produced an
             * unreadable text smear (confirmed via a live screenshot). `RankPanel` already lists
             * every sector's name unambiguously in 2D; the 3D label's only job is confirming
             * WHICH badge you're pointing at, so it only needs to exist while hovered. */}
            {isHovered && (
              <SectorLabel
                position={badge.labelPosition}
                name={badge.industryName}
                color={badge.color}
                hovered
              />
            )}
          </group>
        );
      })}
      <AutoRotateController userInteracting={userInteracting} reducedMotion={reducedMotion} />
    </>
  );
}

/**
 * The R3F `<Canvas>` root for 板塊輪動 — composes the floor/ring/starfield/badges/tails/labels
 * declared above, plus drei's `OrbitControls` (never a hand-rolled camera rig, per the reference
 * spec's explicit instruction). Camera auto-orbits slowly when idle and pauses the instant the
 * user starts dragging (tracked via `OrbitControls`'s own `onStart`/`onEnd`), resuming once they
 * release — `AutoRotateController` and `OrbitControls`' own user-drag both write the same
 * `camera.position`, so they must never run in the same frame (`userInteracting` gates it).
 */
export function RotationCanvas({
  sectors,
  currentWeek,
  hoveredSectorId,
  reducedMotion,
}: {
  readonly sectors: readonly SectorRrgSeries[];
  readonly currentWeek: number;
  readonly hoveredSectorId: string | null;
  readonly reducedMotion: boolean;
}): ReactNode {
  const [userInteracting, setUserInteracting] = useState(false);

  return (
    <Canvas camera={{ position: [...CAMERA_START], fov: 45 }} dpr={[1, 2]}>
      <SceneContents
        sectors={sectors}
        currentWeek={currentWeek}
        hoveredSectorId={hoveredSectorId}
        reducedMotion={reducedMotion}
        userInteracting={userInteracting}
      />
      <OrbitControls
        enablePan={false}
        minPolarAngle={0.15}
        maxPolarAngle={Math.PI / 2.05}
        minDistance={6}
        maxDistance={26}
        onStart={() => setUserInteracting(true)}
        onEnd={() => setUserInteracting(false)}
      />
    </Canvas>
  );
}
