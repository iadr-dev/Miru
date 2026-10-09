import { PawPrintIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { PET_ART } from "@/components/pets/pet-art";
import { cn } from "@/lib/cn";
import { RARITY_RING } from "@/lib/pets";
import type { PetRarity } from "@/types/settings";

/** A companion's portrait on a round badge with its rarity ring. `locked` shows only the
 * silhouette (a pet not yet drawn from the gacha); `label` names it for assistive tech, and
 * without one the avatar is decorative. Cosmetic only. */
export function PetAvatar({
  petId,
  rarity,
  size = 40,
  locked = false,
  label,
  className,
}: {
  petId: string;
  rarity: PetRarity;
  size?: number;
  locked?: boolean;
  label?: string;
  className?: string;
}): ReactNode {
  const Art = PET_ART[petId];
  return (
    <span
      style={{ width: size, height: size }}
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full border-2 bg-[var(--surface-2)]",
        RARITY_RING[rarity],
        className,
      )}
    >
      {Art ? (
        <svg
          viewBox="0 0 64 64"
          width={size}
          height={size}
          role={label ? "img" : undefined}
          aria-label={label}
          aria-hidden={label ? undefined : true}
          className={locked ? "opacity-35 grayscale" : undefined}
        >
          {label ? <title>{label}</title> : null}
          <Art />
        </svg>
      ) : (
        <PawPrintIcon size={size * 0.5} weight="regular" aria-hidden />
      )}
    </span>
  );
}
