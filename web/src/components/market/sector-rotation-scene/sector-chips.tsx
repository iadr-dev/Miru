"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import { shortSectorName } from "./rrg-layout";

export interface SectorChip {
  readonly industryId: string;
  readonly industryName: string;
  readonly color: string;
}

/** One chip per sector, tinted with the sector colour its head and trail use. Hovering previews it in the scene and
 * the rank list; clicking selects it (click again to clear). A real button, so the whole
 * selection is reachable by keyboard, not just by pointing at a badge. */
export function SectorChips({
  chips,
  selectedSectorId,
  hoveredSectorId,
  onSelect,
  onHoverChange,
}: {
  readonly chips: readonly SectorChip[];
  readonly selectedSectorId: string | null;
  readonly hoveredSectorId: string | null;
  readonly onSelect: (id: string | null) => void;
  readonly onHoverChange: (id: string | null) => void;
}): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap.rotationRrg");
  return (
    <div
      role="group"
      aria-label={t("chipsLabel")}
      className="pointer-events-auto flex flex-wrap gap-1.5"
    >
      {chips.map((chip) => {
        const selected = selectedSectorId === chip.industryId;
        const active = selected || hoveredSectorId === chip.industryId;
        const color = chip.color;
        return (
          <button
            key={chip.industryId}
            type="button"
            aria-pressed={selected}
            title={chip.industryName}
            onClick={() => onSelect(selected ? null : chip.industryId)}
            onMouseEnter={() => onHoverChange(chip.industryId)}
            onMouseLeave={() => onHoverChange(null)}
            onFocus={() => onHoverChange(chip.industryId)}
            onBlur={() => onHoverChange(null)}
            className={cn(
              "flex h-8 items-center gap-1.5 rounded-full border px-2.5 text-[12px] transition-colors active:scale-95",
              active
                ? "border-transparent bg-[var(--surface-2)] text-[var(--text)]"
                : "border-[var(--border)] bg-[var(--surface)]/60 text-[var(--text-muted)]",
            )}
            style={selected ? { boxShadow: `inset 0 0 0 1.5px ${color}` } : undefined}
          >
            <span aria-hidden className="size-2 rounded-full" style={{ background: color }} />
            {shortSectorName(chip.industryName)}
          </button>
        );
      })}
    </div>
  );
}
