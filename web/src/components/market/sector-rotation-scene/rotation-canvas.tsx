"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, useFrame } from "@react-three/fiber";
import { type ReactNode, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { sectorIconKeyForIndustry } from "@/lib/sector-rotation-icons";
import type { SectorRrgSeries } from "@/types/market/sectors";
import { CometTail } from "./comet-tail";
import { CompassRing } from "./compass-ring";
import { QuadrantFloor } from "./quadrant-floor";
import type { RotationView } from "./rotation-controls";
import {
  colorForQuadrant,
  layoutLabels,
  lerpPoint,
  quadrantFor,
  rrgToWorld,
  shortSectorName,
} from "./rrg-layout";
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
// Straight down onto the quadrant plane; the tiny z keeps `lookAt` away from the degenerate pole.
const CAMERA_FLAT: readonly [number, number, number] = [0, 19, 0.01];
const AUTO_ROTATE_RADIANS_PER_SECOND = 0.05;
// Bigger than the tilted view's 10: from straight above the names have room to be readable.
const FLAT_LABEL_DISTANCE_FACTOR = 16;
const CAMERA_GLIDE_PER_FRAME = 0.12;
const CAMERA_ARRIVED_DISTANCE = 0.02;

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

/** Glides the camera to the flat top-down spot or back to the tilted one when the view changes
 * (snaps with reduced motion), then lets go so the user's own orbit/zoom is never fought. */
function CameraRig({
  view,
  reducedMotion,
}: {
  readonly view: RotationView;
  readonly reducedMotion: boolean;
}): ReactNode {
  const target = useRef(new THREE.Vector3(...(view === "flat" ? CAMERA_FLAT : CAMERA_START)));
  const gliding = useRef(false);
  const lastView = useRef(view);

  useFrame(({ camera }) => {
    if (lastView.current !== view) {
      lastView.current = view;
      target.current.set(...(view === "flat" ? CAMERA_FLAT : CAMERA_START));
      gliding.current = true;
    }
    if (!gliding.current) return;
    if (reducedMotion) {
      camera.position.copy(target.current);
    } else {
      camera.position.lerp(target.current, CAMERA_GLIDE_PER_FRAME);
    }
    camera.lookAt(0, 0, 0);
    if (camera.position.distanceTo(target.current) < CAMERA_ARRIVED_DISTANCE) {
      camera.position.copy(target.current);
      gliding.current = false;
    }
  });
  return null;
}

function AutoRotateController({
  userInteracting,
  reducedMotion,
  view,
}: {
  readonly userInteracting: boolean;
  readonly reducedMotion: boolean;
  readonly view: RotationView;
}): ReactNode {
  useFrame(({ camera }, delta) => {
    if (userInteracting || reducedMotion || view === "flat") return;
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
  selectedSectorId,
  view,
  reducedMotion,
  userInteracting,
  onHoverChange,
  onSelect,
}: {
  readonly sectors: readonly SectorRrgSeries[];
  readonly currentWeek: number;
  readonly hoveredSectorId: string | null;
  readonly selectedSectorId: string | null;
  readonly view: RotationView;
  readonly reducedMotion: boolean;
  readonly userInteracting: boolean;
  readonly onHoverChange: (id: string | null) => void;
  readonly onSelect: (id: string | null) => void;
}): ReactNode {
  const introProgress = useIntroProgress(reducedMotion);
  const weekCount = sectors[0]?.points.length ?? 0;
  const playbackFraction = weekCount > 1 ? currentWeek / (weekCount - 1) : 0;

  const badgeData = useMemo(() => {
    const floorWeek = Math.floor(currentWeek);
    const frac = currentWeek - floorWeek;
    const heads = sectors.flatMap((sector) => {
      const a = sector.points[floorWeek];
      const b = sector.points[Math.min(floorWeek + 1, sector.points.length - 1)];
      if (!a || !b) return [];
      const interpolated = lerpPoint(a, b, frac);
      const { x, z } = rrgToWorld(interpolated.rs_ratio, interpolated.rs_momentum);
      return [
        {
          sector,
          x,
          z,
          quadrant: quadrantFor(interpolated.rs_ratio, interpolated.rs_momentum),
        },
      ];
    });
    const offsets = layoutLabels(
      heads.map((head) => ({
        id: head.sector.industry_id,
        name: shortSectorName(head.sector.industry_name),
        x: head.x,
        z: head.z,
      })),
    );
    return heads.map((head) => {
      const offset = offsets.get(head.sector.industry_id) ?? { dx: 0, dz: 0 };
      return {
        industryId: head.sector.industry_id,
        shortName: shortSectorName(head.sector.industry_name),
        weight: head.sector.weight,
        position: [head.x, BADGE_HEIGHT, head.z] as const,
        labelPosition: [head.x + offset.dx, LABEL_HEIGHT, head.z + offset.dz] as const,
        color: colorForQuadrant(head.quadrant),
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
        const isHovered = hoveredSectorId === badge.industryId;
        const isSelected = selectedSectorId === badge.industryId;
        // The flat view names every sector (placed so the names do not overlap); the tilted view
        // keeps names to the hovered/selected one, where perspective would pile them up.
        const showLabel = view === "flat" || isHovered || isSelected;
        return (
          <group key={badge.industryId}>
            <SectorBadge
              position={badge.position}
              icon={sectorIconKeyForIndustry(badge.industryId)}
              color={badge.color}
              weight={badge.weight}
              hovered={isHovered || isSelected}
              introProgress={introProgress}
              onHoverChange={(over) => onHoverChange(over ? badge.industryId : null)}
              onSelect={() => onSelect(isSelected ? null : badge.industryId)}
            />
            {showLabel && (
              <SectorLabel
                position={badge.labelPosition}
                name={badge.shortName}
                color={badge.color}
                hovered={isHovered || isSelected}
                distanceFactor={view === "flat" ? FLAT_LABEL_DISTANCE_FACTOR : undefined}
              />
            )}
          </group>
        );
      })}
      <CameraRig view={view} reducedMotion={reducedMotion} />
      <AutoRotateController
        userInteracting={userInteracting}
        reducedMotion={reducedMotion}
        view={view}
      />
    </>
  );
}

/**
 * The R3F `<Canvas>` root for 板塊輪動 — composes the floor/ring/starfield/badges/tails/labels
 * declared above, plus drei's `OrbitControls` (never a hand-rolled camera rig, per the reference
 * spec's explicit instruction). The flat view looks straight down (rotation locked, zoom kept);
 * the 3D view auto-orbits slowly when idle and pauses the instant the user starts dragging
 * (tracked via `OrbitControls`'s own `onStart`/`onEnd`) — `AutoRotateController`, `CameraRig`
 * and `OrbitControls`' own user-drag all write the same `camera.position`, so they never run in
 * the same frame (`userInteracting` and the glide's own arrival gate it).
 */
export function RotationCanvas({
  sectors,
  currentWeek,
  hoveredSectorId,
  selectedSectorId,
  view,
  reducedMotion,
  onHoverChange,
  onSelect,
}: {
  readonly sectors: readonly SectorRrgSeries[];
  readonly currentWeek: number;
  readonly hoveredSectorId: string | null;
  readonly selectedSectorId: string | null;
  readonly view: RotationView;
  readonly reducedMotion: boolean;
  readonly onHoverChange: (id: string | null) => void;
  readonly onSelect: (id: string | null) => void;
}): ReactNode {
  const [userInteracting, setUserInteracting] = useState(false);
  const flat = view === "flat";

  return (
    <Canvas
      camera={{ position: [...(flat ? CAMERA_FLAT : CAMERA_START)], fov: 45 }}
      dpr={[1, 2]}
      onPointerMissed={() => onSelect(null)}
    >
      <SceneContents
        sectors={sectors}
        currentWeek={currentWeek}
        hoveredSectorId={hoveredSectorId}
        selectedSectorId={selectedSectorId}
        view={view}
        reducedMotion={reducedMotion}
        userInteracting={userInteracting}
        onHoverChange={onHoverChange}
        onSelect={onSelect}
      />
      <OrbitControls
        enablePan={false}
        enableRotate={!flat}
        minPolarAngle={flat ? 0 : 0.15}
        maxPolarAngle={Math.PI / 2.05}
        minDistance={6}
        maxDistance={26}
        onStart={() => setUserInteracting(true)}
        onEnd={() => setUserInteracting(false)}
      />
    </Canvas>
  );
}
