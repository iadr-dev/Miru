"use client";

import { Html } from "@react-three/drei";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * A sector's name as a small pill pinned to its head, via drei's `Html` (a screen-space HTML
 * element rather than a texture, so the text stays crisp at any zoom and the pill is clickable).
 * The pill is tinted with the sector's own colour; `dimmed` fades it when another sector is
 * selected, `emphasized` (hovered or selected) brings it to full strength.
 */
export function SectorLabel({
  position,
  name,
  color,
  emphasized,
  dimmed,
  onHoverChange,
  onSelect,
}: {
  readonly position: readonly [number, number, number];
  readonly name: string;
  readonly color: string;
  readonly emphasized: boolean;
  readonly dimmed: boolean;
  readonly onHoverChange: (hovered: boolean) => void;
  readonly onSelect: () => void;
}): ReactNode {
  return (
    <Html position={[...position]} center zIndexRange={[10, 0]}>
      <button
        type="button"
        tabIndex={-1}
        onClick={onSelect}
        onMouseEnter={() => onHoverChange(true)}
        onMouseLeave={() => onHoverChange(false)}
        className={cn(
          "-translate-y-[0.1rem] cursor-pointer whitespace-nowrap rounded-full border bg-[var(--bg)]/65 px-1.5 py-0.5 font-extrabold text-[12px] leading-none transition-opacity duration-200",
          dimmed ? "opacity-35" : "opacity-100",
          emphasized ? "bg-[var(--surface-2)]" : null,
        )}
        style={{ color, borderColor: "currentcolor" }}
      >
        {name}
      </button>
    </Html>
  );
}
