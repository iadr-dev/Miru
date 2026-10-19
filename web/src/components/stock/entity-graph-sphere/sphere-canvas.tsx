"use client";

import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { type ReactNode, useCallback, useEffect, useRef } from "react";
import { type Group, MathUtils } from "three";
import { frontRotation, nearestAngle } from "@/lib/entity-graph-sphere";
import { SCENE_HEX } from "./palette";
import type { SphereSceneProps } from "./scene-types";
import { SphereLink } from "./sphere-links";
import { SphereNode } from "./sphere-node";

const FOV = 40;
/** Room kept around the unit sphere (its nodes and their names) when fitting the camera, in units. */
const FIT_RADIUS = 1.5;
/** The smallest hit area radius on screen: 22px, so every tap target is at least 44px wide. */
const HIT_RADIUS_PX = 22;
/** Below this canvas width only the nodes nearest the front are named; the list names the rest. */
const COMPACT_WIDTH = 480;
const LABEL_MIN_Z = 0.15;
const LABEL_MIN_Z_COMPACT = 0.55;

/** Idle turn, radians per second: one revolution a minute or so. Constant, never read from data. */
const IDLE_SPEED = 0.1;
const DRAG_SPEED = 0.007;
const MAX_PITCH = 1.45;
/** Easing rate for rotate-to-front (higher is quicker). */
const EASE_RATE = 7;
const SETTLED = 0.002;
/** Longest step one frame may take, seconds: a frame after a long idle must not jump. */
const MAX_STEP = 0.05;

type Motion = {
  yaw: number;
  pitch: number;
  target: { yaw: number; pitch: number } | null;
  dragging: boolean;
  /** Any drag, tap or selection ends the idle turn for good. */
  interacted: boolean;
};

function clampPitch(pitch: number): number {
  return Math.min(MAX_PITCH, Math.max(-MAX_PITCH, pitch));
}

function SphereScene({
  items,
  links,
  selectedId,
  paused,
  reducedMotion,
  onHover,
  onSelect,
}: SphereSceneProps): ReactNode {
  const gl = useThree((state) => state.gl);
  const camera = useThree((state) => state.camera);
  const size = useThree((state) => state.size);
  const invalidate = useThree((state) => state.invalidate);
  const groupRef = useRef<Group>(null);
  const motion = useRef<Motion>({
    yaw: 0,
    pitch: 0,
    target: null,
    dragging: false,
    interacted: false,
  });
  const idleAllowed = useRef(false);
  idleAllowed.current = !paused && !reducedMotion;
  // Read through a ref so hovering (which rebuilds `items`) does not re-run the effects below.
  const itemsRef = useRef(items);
  itemsRef.current = items;

  // Fit the whole sphere in the canvas whichever side is shorter.
  const halfFov = MathUtils.degToRad(FOV / 2);
  const aspect = size.height > 0 ? size.width / size.height : 1;
  const distance = FIT_RADIUS / (Math.tan(halfFov) * Math.min(1, aspect));
  useEffect(() => {
    camera.position.set(0, 0, distance);
    camera.updateProjectionMatrix();
    invalidate();
  }, [camera, distance, invalidate]);
  const pxPerUnit = size.height / (2 * distance * Math.tan(halfFov));
  // Sized for the farthest nodes, which are the smallest on screen.
  const hitRadius = (HIT_RADIUS_PX * 2 * (distance + 1) * Math.tan(halfFov)) / size.height;
  const labelMinZ = size.width < COMPACT_WIDTH ? LABEL_MIN_Z_COMPACT : LABEL_MIN_Z;

  const turnToFront = useCallback(
    (id: string): void => {
      const item = itemsRef.current.find((candidate) => candidate.placed.node.id === id);
      if (!item) return;
      const state = motion.current;
      const front = frontRotation(item.placed.position);
      state.target = {
        yaw: nearestAngle(state.yaw, front.yaw),
        pitch: clampPitch(front.pitch),
      };
      state.interacted = true;
      invalidate();
    },
    [invalidate],
  );

  // A node chosen elsewhere (the list under the graph) is brought round too.
  useEffect(() => {
    if (selectedId !== null) turnToFront(selectedId);
  }, [selectedId, turnToFront]);

  // The idle turn starts and stops with the pointer, focus and visibility.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the deps are the triggers, not reads.
  useEffect(() => {
    invalidate();
  }, [paused, reducedMotion, invalidate]);

  // Drag to rotate. Touch turns around the vertical axis only and leaves vertical swipes to the
  // page (`touch-action: pan-y`), so a finger on the sphere never traps the scroll.
  useEffect(() => {
    const element = gl.domElement;
    let last: { x: number; y: number } | null = null;
    const onDown = (event: PointerEvent): void => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      last = { x: event.clientX, y: event.clientY };
      motion.current.dragging = true;
      motion.current.interacted = true;
      motion.current.target = null;
      element.style.cursor = "grabbing";
      element.setPointerCapture(event.pointerId);
    };
    const onMove = (event: PointerEvent): void => {
      if (!last || !motion.current.dragging) return;
      const state = motion.current;
      state.yaw += (event.clientX - last.x) * DRAG_SPEED;
      if (event.pointerType !== "touch") {
        state.pitch = clampPitch(state.pitch + (event.clientY - last.y) * DRAG_SPEED);
      }
      last = { x: event.clientX, y: event.clientY };
      invalidate();
    };
    const onEnd = (event: PointerEvent): void => {
      if (element.hasPointerCapture(event.pointerId))
        element.releasePointerCapture(event.pointerId);
      motion.current.dragging = false;
      last = null;
      element.style.cursor = "";
    };
    element.addEventListener("pointerdown", onDown);
    element.addEventListener("pointermove", onMove);
    element.addEventListener("pointerup", onEnd);
    element.addEventListener("pointercancel", onEnd);
    return () => {
      element.removeEventListener("pointerdown", onDown);
      element.removeEventListener("pointermove", onMove);
      element.removeEventListener("pointerup", onEnd);
      element.removeEventListener("pointercancel", onEnd);
    };
  }, [gl, invalidate]);

  useFrame((_, rawDelta) => {
    const group = groupRef.current;
    if (!group) return;
    const state = motion.current;
    const delta = Math.min(rawDelta, MAX_STEP);
    let moving = false;
    const goal = state.target;
    if (goal && !state.dragging) {
      if (reducedMotion) {
        state.yaw = goal.yaw;
        state.pitch = goal.pitch;
        state.target = null;
      } else {
        state.yaw = MathUtils.damp(state.yaw, goal.yaw, EASE_RATE, delta);
        state.pitch = MathUtils.damp(state.pitch, goal.pitch, EASE_RATE, delta);
        const settled =
          Math.abs(state.yaw - goal.yaw) < SETTLED && Math.abs(state.pitch - goal.pitch) < SETTLED;
        if (settled) {
          state.yaw = goal.yaw;
          state.pitch = goal.pitch;
          state.target = null;
        } else {
          moving = true;
        }
      }
    } else if (!state.dragging && !state.interacted && idleAllowed.current) {
      state.yaw += IDLE_SPEED * delta;
      moving = true;
    }
    group.rotation.set(state.pitch, state.yaw, 0);
    if (moving) invalidate();
  });

  const pick = (id: string): void => {
    turnToFront(id);
    const item = itemsRef.current.find((candidate) => candidate.placed.node.id === id);
    // The company in focus is the subject of the page: it comes to the front but is not a detail.
    if (item && !item.placed.node.focus) onSelect(id);
  };

  return (
    <>
      <ambientLight intensity={1.15} />
      <directionalLight position={[2, 3, 5]} intensity={1.7} />
      <group ref={groupRef}>
        <mesh>
          <sphereGeometry args={[0.985, 24, 16]} />
          <meshBasicMaterial
            color={SCENE_HEX.guide}
            wireframe
            transparent
            opacity={0.1}
            depthWrite={false}
          />
        </mesh>
        {links.map((link) => (
          <SphereLink key={link.key} link={link} />
        ))}
        {items.map((item) => (
          <SphereNode
            key={item.placed.node.id}
            item={item}
            groupRef={groupRef}
            labelMinZ={labelMinZ}
            pxPerUnit={pxPerUnit}
            hitRadius={Math.max(hitRadius, item.placed.size * 1.15)}
            onHover={onHover}
            onPick={pick}
          />
        ))}
      </group>
    </>
  );
}

/** The R3F canvas for the sphere view. Frames are drawn on demand (while it turns, eases or is
 * dragged), so a still sphere costs nothing. */
export function SphereCanvas(props: SphereSceneProps): ReactNode {
  return (
    <Canvas
      frameloop="demand"
      dpr={[1, 2]}
      camera={{ fov: FOV, near: 0.1, far: 30, position: [0, 0, 4] }}
      style={{ touchAction: "pan-y" }}
    >
      <SphereScene {...props} />
    </Canvas>
  );
}
