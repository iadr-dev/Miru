"use client";

import { Html } from "@react-three/drei";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * A sector's name pinned in 3D space just above its badge, via drei's `Html` (billboarded HTML
 * overlay rather than a texture-baked label — text stays crisp at any zoom, unlike a canvas
 * texture). `hovered` brightens/bolds the label to mirror `RankPanel`'s row-hover delegation —
 * the same sector highlighted in the list should visibly "wake up" in the 3D scene too, not just
 * scale its badge.
 */
export function SectorLabel({
  position,
  name,
  color,
  hovered,
}: {
  readonly position: readonly [number, number, number];
  readonly name: string;
  readonly color: string;
  readonly hovered: boolean;
}): ReactNode {
  return (
    <Html position={position} center distanceFactor={10} zIndexRange={[10, 0]}>
      <span
        className={cn(
          "whitespace-nowrap font-medium text-[11px] transition-all duration-150",
          hovered ? "font-semibold text-[13px]" : "opacity-80",
        )}
        style={{ color: hovered ? color : "#e6eaf0" }}
      >
        {name}
      </span>
    </Html>
  );
}
