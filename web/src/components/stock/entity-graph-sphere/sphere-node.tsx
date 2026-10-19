"use client";

import { Html, RoundedBox } from "@react-three/drei";
import { type ThreeEvent, useFrame, useThree } from "@react-three/fiber";
import { type ReactNode, type RefObject, useCallback, useEffect, useRef } from "react";
import { type Group, type MeshStandardMaterial, Vector3 } from "three";
import { labelOpacity, nodeOpacity } from "@/lib/entity-graph-sphere";
import { SCENE_HEX } from "./palette";
import type { SphereItem } from "./scene-types";

const DIMMED_FACTOR = 0.3;
/** A cube of side `CUBE_SIDE * size` has roughly the footprint of a sphere of radius `size`. */
const CUBE_SIDE = 1.7;
const CUBE_TURN: readonly [number, number, number] = [0.5, 0.6, 0];

/**
 * One node: a sphere for a company, a rounded cube for any other entity (a shape cue, so colour is
 * never the only one), its name as a DOM label and an invisible, larger hit area so a tap target
 * is at least 44px whatever the node's own size. Depth is read from the sphere's current turn
 * every frame and written straight to the material and the label, never through React state:
 * nodes on the far side dim, and only the front hemisphere shows names.
 */
export function SphereNode({
  item,
  groupRef,
  labelMinZ,
  pxPerUnit,
  hitRadius,
  onHover,
  onPick,
}: {
  readonly item: SphereItem;
  readonly groupRef: RefObject<Group | null>;
  readonly labelMinZ: number;
  readonly pxPerUnit: number;
  readonly hitRadius: number;
  readonly onHover: (id: string | null) => void;
  readonly onPick: (id: string) => void;
}): ReactNode {
  const { node, position, size } = item.placed;
  const gl = useThree((state) => state.gl);
  const invalidate = useThree((state) => state.invalidate);
  const materialRef = useRef<MeshStandardMaterial | null>(null);
  const labelRef = useRef<HTMLDivElement | null>(null);
  const scratch = useRef(new Vector3());
  const raised = item.active || item.matched;
  const scale = raised ? 1.22 : 1;

  // The label lives in its own React root (drei `Html`), so it can appear after the first frame.
  const setLabel = useCallback(
    (element: HTMLDivElement | null) => {
      labelRef.current = element;
      if (element) invalidate();
    },
    [invalidate],
  );

  // Frames only run on demand: a change that is not a prop of a three object needs a nudge.
  // biome-ignore lint/correctness/useExhaustiveDependencies: the deps are the triggers, not reads.
  useEffect(() => {
    invalidate();
  }, [item.dimmed, item.matched, item.active, labelMinZ, invalidate]);

  useFrame(() => {
    const group = groupRef.current;
    if (!group) return;
    const z = scratch.current
      .set(position[0], position[1], position[2])
      .applyQuaternion(group.quaternion).z;
    const dim = item.dimmed ? DIMMED_FACTOR : 1;
    if (materialRef.current) materialRef.current.opacity = nodeOpacity(z) * dim;
    if (labelRef.current) labelRef.current.style.opacity = String(labelOpacity(z, labelMinZ) * dim);
  });

  const over = (event: ThreeEvent<PointerEvent>): void => {
    if (event.nativeEvent.pointerType === "touch") return;
    event.stopPropagation();
    gl.domElement.style.cursor = "pointer";
    onHover(node.id);
  };
  const out = (event: ThreeEvent<PointerEvent>): void => {
    if (event.nativeEvent.pointerType === "touch") return;
    gl.domElement.style.cursor = "";
    onHover(null);
  };
  const pick = (event: ThreeEvent<MouseEvent>): void => {
    event.stopPropagation();
    onPick(node.id);
  };

  const materialProps = {
    ref: materialRef,
    color: item.color,
    emissive: item.color,
    emissiveIntensity: raised ? 0.55 : node.focus ? 0.3 : 0.12,
    roughness: 0.45,
    metalness: 0.1,
    transparent: true,
  } as const;

  return (
    <group position={[position[0], position[1], position[2]]}>
      <group scale={scale}>
        {item.company ? (
          <mesh>
            <sphereGeometry args={[size, 28, 18]} />
            <meshStandardMaterial {...materialProps} />
          </mesh>
        ) : (
          <RoundedBox
            args={[size * CUBE_SIDE, size * CUBE_SIDE, size * CUBE_SIDE]}
            radius={size * 0.32}
            smoothness={3}
            rotation={[...CUBE_TURN]}
          >
            <meshStandardMaterial {...materialProps} />
          </RoundedBox>
        )}
      </group>
      {/* biome-ignore lint/a11y/noStaticElementInteractions: a three.js mesh, not a DOM element; the list under the graph is the keyboard path. */}
      <mesh onPointerOver={over} onPointerOut={out} onClick={pick}>
        <sphereGeometry args={[hitRadius, 10, 8]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} />
      </mesh>
      <Html center pointerEvents="none" zIndexRange={[10, 0]}>
        <div
          ref={setLabel}
          className="whitespace-nowrap rounded-full px-1.5 py-0.5 font-medium text-[11px] leading-none"
          style={{
            opacity: 0,
            marginTop: size * pxPerUnit * scale + 12,
            color: SCENE_HEX.labelText,
            backgroundColor: SCENE_HEX.labelBackground,
            fontSize: node.focus ? 13 : 11,
            fontWeight: node.focus ? 600 : 500,
          }}
        >
          {item.label}
        </div>
      </Html>
    </group>
  );
}
