"use client";

import { OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import type { SectorRrgSeries } from "@/types/market/sectors";
import { RotationBox } from "./rotation-box";
import type { RotationView } from "./rotation-controls";
import { layoutLabels, type RotationQuadrant, shortSectorName } from "./rrg-layout";
import { SectorLabel } from "./sector-label";
import { headPosition, SectorTrail, TRAIL_WEEKS, type TrailEmphasis } from "./sector-trail";

const FOV = 42;
// The tilted view looks at the middle of the time axis from a fixed orbit.
const TILTED_TARGET = new THREE.Vector3(0, 0, -10);
const TILTED_AZIMUTH = 0.6;
const TILTED_POLAR = 1.3;
const TILTED_DISTANCE = 44;
const TILTED_DISTANCE_NARROW = 62;
// Straight on to the plane: close enough that its 20 units fill the view's height.
const FLAT_DISTANCE = 28;
const FLAT_DISTANCE_NARROW = 46;
const NARROW_ASPECT = 0.8;
const GLIDE_PER_FRAME = 0.12;
const ARRIVED = 0.02;
const AUTO_ROTATE_SPEED = 1.1;
// Labels are placed on a grid where a character is a fixed width; this converts the scene's
// world units to that grid (one world unit is about 25px at the default zoom).
const LABEL_GRID = 1.5;
const LABEL_LIFT = 0.9;

interface ViewPose {
  readonly position: THREE.Vector3;
  readonly target: THREE.Vector3;
}

function poseFor(view: RotationView, aspect: number): ViewPose {
  const narrow = aspect < NARROW_ASPECT;
  if (view === "flat") {
    return {
      position: new THREE.Vector3(0, 0, narrow ? FLAT_DISTANCE_NARROW : FLAT_DISTANCE),
      target: new THREE.Vector3(0, 0, 0),
    };
  }
  const distance = narrow ? TILTED_DISTANCE_NARROW : TILTED_DISTANCE;
  const offset = new THREE.Vector3(
    Math.sin(TILTED_POLAR) * Math.sin(TILTED_AZIMUTH),
    Math.cos(TILTED_POLAR),
    Math.sin(TILTED_POLAR) * Math.cos(TILTED_AZIMUTH),
  ).multiplyScalar(distance);
  return { position: TILTED_TARGET.clone().add(offset), target: TILTED_TARGET.clone() };
}

/** Moves the camera to the flat or tilted pose when the view changes (snaps with reduced
 * motion), then lets go, so the user's own orbit/zoom is never fought. */
function CameraRig({
  view,
  reducedMotion,
}: {
  readonly view: RotationView;
  readonly reducedMotion: boolean;
}): ReactNode {
  const { camera, size } = useThree();
  const controls = useThree((state) => state.controls) as {
    target: THREE.Vector3;
    update: () => void;
  } | null;
  const goal = useRef<ViewPose | null>(null);
  const shown = useRef<RotationView | null>(null);

  useFrame(() => {
    if (!controls) return;
    if (shown.current !== view) {
      const first = shown.current === null;
      shown.current = view;
      goal.current = poseFor(view, size.width / size.height);
      if (first || reducedMotion) {
        camera.position.copy(goal.current.position);
        controls.target.copy(goal.current.target);
        controls.update();
        goal.current = null;
        return;
      }
    }
    const pose = goal.current;
    if (!pose) return;
    camera.position.lerp(pose.position, GLIDE_PER_FRAME);
    controls.target.lerp(pose.target, GLIDE_PER_FRAME);
    controls.update();
    const arrived =
      camera.position.distanceTo(pose.position) < ARRIVED &&
      controls.target.distanceTo(pose.target) < ARRIVED;
    if (arrived) {
      camera.position.copy(pose.position);
      controls.target.copy(pose.target);
      controls.update();
      goal.current = null;
    }
  });
  return null;
}

function emphasisFor(
  id: string,
  hoveredSectorId: string | null,
  selectedSectorId: string | null,
): TrailEmphasis {
  if (selectedSectorId === id) return "selected";
  if (selectedSectorId !== null) return "dimmed";
  if (hoveredSectorId === id) return "hovered";
  return "normal";
}

interface SceneProps {
  readonly sectors: readonly SectorRrgSeries[];
  readonly colors: readonly string[];
  readonly currentWeek: number;
  readonly range: number;
  readonly hoveredSectorId: string | null;
  readonly selectedSectorId: string | null;
  readonly view: RotationView;
  readonly quadrantLabels: Record<RotationQuadrant, string>;
  readonly onHoverChange: (id: string | null) => void;
  readonly onSelect: (id: string | null) => void;
}

function SceneContents({
  sectors,
  colors,
  currentWeek,
  range,
  hoveredSectorId,
  selectedSectorId,
  view,
  quadrantLabels,
  onHoverChange,
  onSelect,
}: SceneProps): ReactNode {
  // Where each name goes. The flat view spreads names that would land on each other; the tilted
  // view keeps them straight above their heads, because perspective moves them anyway.
  const labelAt = useMemo(() => {
    const heads = sectors.flatMap((sector) => {
      const head = headPosition(sector.points, currentWeek, range);
      return head
        ? [
            {
              id: sector.industry_id,
              name: shortSectorName(sector.industry_name),
              x: head.x / LABEL_GRID,
              z: -head.y / LABEL_GRID,
              headX: head.x,
              headY: head.y,
            },
          ]
        : [];
    });
    const offsets = view === "flat" ? layoutLabels(heads) : new Map();
    return new Map(
      heads.map((head) => {
        const offset = offsets.get(head.id) ?? { dx: 0, dz: -LABEL_LIFT / LABEL_GRID };
        return [
          head.id,
          [head.headX + offset.dx * LABEL_GRID, head.headY - offset.dz * LABEL_GRID, 0] as const,
        ];
      }),
    );
  }, [sectors, currentWeek, range, view]);

  return (
    <>
      <RotationBox weekCount={TRAIL_WEEKS} quadrantLabels={quadrantLabels} />
      {sectors.map((sector, index) => {
        const id = sector.industry_id;
        const color = colors[index] ?? "#60a5fa";
        const emphasis = emphasisFor(id, hoveredSectorId, selectedSectorId);
        const labelPosition = labelAt.get(id);
        return (
          <group key={id}>
            <SectorTrail
              points={sector.points}
              color={color}
              currentWeek={currentWeek}
              range={range}
              emphasis={emphasis}
              onHoverChange={(over) => onHoverChange(over ? id : null)}
              onSelect={() => onSelect(selectedSectorId === id ? null : id)}
            />
            {labelPosition ? (
              <SectorLabel
                position={labelPosition}
                name={shortSectorName(sector.industry_name)}
                color={color}
                emphasized={emphasis === "selected" || emphasis === "hovered"}
                dimmed={emphasis === "dimmed"}
                onHoverChange={(over) => onHoverChange(over ? id : null)}
                onSelect={() => onSelect(selectedSectorId === id ? null : id)}
              />
            ) : null}
          </group>
        );
      })}
    </>
  );
}

/**
 * The R3F `<Canvas>` for 板塊輪動. The flat view looks straight at the quadrant plane (rotation
 * locked, zoom kept); the tilted view orbits the time-box and turns slowly until the user takes
 * hold of it (as in the reference design), after which it stays where they put it.
 * `quadrantLabels` come in as props because React context (next-intl) does not cross the
 * `<Canvas>` boundary.
 */
export function RotationCanvas(props: SceneProps & { readonly reducedMotion: boolean }): ReactNode {
  const { view, reducedMotion, onSelect, ...scene } = props;
  const flat = view === "flat";
  const [autoRotate, setAutoRotate] = useState(true);

  // Every return to the tilted view starts turning again; a touch stops it.
  useEffect(() => {
    if (view === "3d") setAutoRotate(true);
  }, [view]);

  return (
    <Canvas
      camera={{ fov: FOV, near: 0.1, far: 300, position: [0, 0, FLAT_DISTANCE] }}
      dpr={[1, 2]}
      onPointerMissed={() => onSelect(null)}
    >
      <SceneContents view={view} onSelect={onSelect} {...scene} />
      <OrbitControls
        makeDefault
        enablePan={false}
        enableRotate={!flat}
        enableDamping
        minPolarAngle={0.25}
        maxPolarAngle={2.9}
        minDistance={20}
        maxDistance={80}
        autoRotate={autoRotate && !flat && !reducedMotion}
        autoRotateSpeed={AUTO_ROTATE_SPEED}
        onStart={() => setAutoRotate(false)}
      />
      <CameraRig view={view} reducedMotion={reducedMotion} />
    </Canvas>
  );
}
