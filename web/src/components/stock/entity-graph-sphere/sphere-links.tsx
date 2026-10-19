"use client";

import { Line } from "@react-three/drei";
import { type ReactNode, useMemo } from "react";
import { Quaternion, Vector3 } from "three";
import { sphereLinkEnds } from "@/lib/entity-graph-sphere";
import { SCENE_HEX } from "./palette";
import type { SphereLinkItem } from "./scene-types";

const ARROW_LENGTH = 0.075;
const ARROW_RADIUS = 0.028;
const UP = new Vector3(0, 1, 0);

/** One link. A supply relationship is a solid line with an arrowhead on the customer (the arrow
 * runs supplier to customer, as in the flat graph); a mention is a thin line; a co-mention is
 * dashed. */
export function SphereLink({ link }: { readonly link: SphereLinkItem }): ReactNode {
  const { start, end, direction } = useMemo(
    () => sphereLinkEnds(link.from, link.to),
    [link.from, link.to],
  );
  const supply = link.kind === "supply";
  const arrow = useMemo(() => {
    const dir = new Vector3(...direction);
    const quaternion = new Quaternion().setFromUnitVectors(UP, dir);
    // The cone is centred on its position; back off half its length so the tip lands on `end`.
    const position = new Vector3(...end).addScaledVector(dir, -ARROW_LENGTH / 2);
    return { quaternion, position };
  }, [direction, end]);

  const lineProps = {
    mention: { color: SCENE_HEX.mention, lineWidth: 1, opacity: 0.6 },
    cooccurrence: { color: SCENE_HEX.mention, lineWidth: 1.25, opacity: 0.75 },
    supply: { color: SCENE_HEX.supply, lineWidth: 2, opacity: 0.95 },
  }[link.kind];

  return (
    <>
      <Line
        points={[start, end]}
        color={lineProps.color}
        lineWidth={lineProps.lineWidth}
        transparent
        opacity={lineProps.opacity}
        depthWrite={false}
        {...(link.kind === "cooccurrence" ? { dashed: true, dashSize: 0.04, gapSize: 0.03 } : {})}
      />
      {supply ? (
        <mesh position={arrow.position} quaternion={arrow.quaternion}>
          <coneGeometry args={[ARROW_RADIUS, ARROW_LENGTH, 12]} />
          <meshBasicMaterial color={SCENE_HEX.supply} />
        </mesh>
      ) : null}
    </>
  );
}
