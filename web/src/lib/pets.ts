import type { PetCatalogEntry, PetProgress, PetRarity } from "@/types/settings";

/** Text colour per rarity (cosmetic only - rarity never means "better answers"). */
export const RARITY_TEXT: Record<PetRarity, string> = {
  common: "text-[var(--text-muted)]",
  // --chart-4-text is the AA-for-text variant of --chart-4.
  uncommon: "text-[var(--chart-4-text)]",
  rare: "text-[var(--chart-1)]",
  legendary: "text-[var(--warn)]",
};

/** Ring colour per rarity for the avatar frame. */
export const RARITY_RING: Record<PetRarity, string> = {
  common: "border-[var(--border)]",
  uncommon: "border-[var(--chart-4)]",
  rare: "border-[var(--chart-1)]",
  legendary: "border-[var(--warn)]",
};

/** The pet's name in the reader's language, falling back to the raw id for a pet this build does
 * not know (an older client against a newer catalog). */
export function petName(
  catalog: readonly PetCatalogEntry[],
  petId: string,
  locale: string,
): string {
  const entry = catalog.find((e) => e.pet_id === petId);
  if (!entry) {
    return petId;
  }
  return locale === "zh-TW" ? entry.name_zh : entry.name;
}

/** How full the current level's XP bar is, 0..1. A pet at max level reads as full. */
export function xpFraction(
  level: number,
  xp: number,
  progress: Pick<PetProgress, "xp_per_level" | "max_level">,
): number {
  if (level >= progress.max_level || progress.xp_per_level <= 0) {
    return 1;
  }
  return Math.min(1, Math.max(0, xp / progress.xp_per_level));
}
