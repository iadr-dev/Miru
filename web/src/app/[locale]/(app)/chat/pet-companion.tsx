"use client";

import { PawPrintIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { PetAvatar } from "@/components/pets/pet-avatar";
import { Link } from "@/i18n/navigation";
import { queryKeys } from "@/lib/api/query-keys";
import { getActivePet, getPetProgress, getPetSettings, listPetCatalog } from "@/lib/api/settings";
import { cn } from "@/lib/cn";
import { FRESHNESS } from "@/lib/freshness";
import { petName, xpFraction } from "@/lib/pets";

export type PetNotice = { leveledTo: number | null; pullsEarned: number };

/** The equipped pet beside the composer: avatar, name, level and a quiet XP bar. It is cosmetic
 * only - it sits outside the answer, never speaks as an analyst, and its only motion is a gentle
 * pulse while Miru is working (off under reduced motion). Without a pet it offers the pull when
 * one is waiting, and otherwise renders nothing. */
export function PetCompanion({
  thinking,
  notice,
}: {
  thinking: boolean;
  notice: PetNotice | null;
}): ReactNode {
  const t = useTranslations("chat.pet");
  const locale = useLocale();
  const active = useQuery({ queryKey: queryKeys.settings("pet-active"), queryFn: getActivePet });
  const progress = useQuery({
    queryKey: queryKeys.settings("pet-progress"),
    queryFn: getPetProgress,
  });
  const settings = useQuery({
    queryKey: queryKeys.settings("pet-settings"),
    queryFn: getPetSettings,
  });
  const catalog = useQuery({
    queryKey: queryKeys.settings("pet-catalog"),
    queryFn: listPetCatalog,
    staleTime: FRESHNESS.static,
  });

  const pet = active.data ?? null;
  const stats = progress.data;
  if (!stats || active.isLoading || settings.data?.companion_visible === false) {
    return null;
  }
  if (!pet) {
    return stats.available_pulls > 0 ? (
      <Link
        href="/settings/pets"
        className="mx-auto mb-2 flex w-fit items-center gap-1.5 text-[12px] text-[var(--accent-text)] underline-offset-2 hover:underline"
      >
        <PawPrintIcon size={14} weight="regular" aria-hidden />
        {t("claim")}
      </Link>
    ) : null;
  }

  const entry = catalog.data?.find((e) => e.pet_id === pet.pet_id);
  const name = petName(catalog.data ?? [], pet.pet_id, locale);
  const fraction = xpFraction(pet.level, pet.xp, stats);
  const message = noticeMessage(notice, t);

  return (
    <div className="mx-auto mb-2 flex w-fit max-w-full items-center gap-2.5 text-[12px] text-[var(--text-muted)] [@media(max-height:500px)]:hidden">
      <PetAvatar
        petId={pet.pet_id}
        rarity={entry?.rarity ?? "common"}
        size={36}
        className={cn(thinking && "motion-safe:animate-pulse")}
      />
      <div className="min-w-0">
        <p className="truncate">
          <span className="text-[var(--text)]">{name}</span>
          <span className="font-num"> · {t("level", { level: pet.level })}</span>
        </p>
        <div
          role="progressbar"
          aria-label={t("xp")}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fraction * 100)}
          className="mt-1 h-1 w-28 overflow-hidden rounded-full bg-[var(--surface-2)]"
        >
          <div
            className="h-full rounded-full bg-[var(--accent)]"
            style={{ width: `${Math.round(fraction * 100)}%` }}
          />
        </div>
      </div>
      {message ? (
        <p role="status" className="text-[var(--text)]">
          {message}
        </p>
      ) : null}
    </div>
  );
}

function noticeMessage(
  notice: PetNotice | null,
  t: (key: string, values?: Record<string, number>) => string,
): string | null {
  if (!notice) {
    return null;
  }
  if (notice.leveledTo !== null) {
    return t("leveled", { level: notice.leveledTo });
  }
  return notice.pullsEarned > 0 ? t("pullEarned", { count: notice.pullsEarned }) : null;
}
