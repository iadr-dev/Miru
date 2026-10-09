"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useState } from "react";
import { ToggleSwitch } from "@/components/settings/form-controls";
import { SettingsRow, SettingsSection } from "@/components/settings/settings-section";
import { SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { Button } from "@/components/ui/button";
import {
  equipPet,
  getActivePet,
  getPetProgress,
  getPetSettings,
  listOwnedPets,
  listPetCatalog,
  pullPet,
  SettingsApiError,
  updatePetSettings,
} from "@/lib/api/settings";
import { cn } from "@/lib/cn";
import { petName, RARITY_TEXT, xpFraction } from "@/lib/pets";
import type { PetCatalogEntry, PetProgress } from "@/types/settings";

/** 夥伴/Pets — gacha/collection grid (cosmetic; reduced-motion → static, no shake/pop).
 * No tier gate. */
export function PetsPanel(): ReactNode {
  const t = useTranslations("settings.pets");
  const locale = useLocale();
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();
  const [pullMessage, setPullMessage] = useState<string | null>(null);

  const catalogQuery = useQuery({ queryKey: ["settings", "pet-catalog"], queryFn: listPetCatalog });
  const ownedQuery = useQuery({ queryKey: ["settings", "pets-owned"], queryFn: listOwnedPets });
  const activeQuery = useQuery({ queryKey: ["settings", "pet-active"], queryFn: getActivePet });
  const progressQuery = useQuery({
    queryKey: ["settings", "pet-progress"],
    queryFn: getPetProgress,
  });

  const settingsQuery = useQuery({
    queryKey: ["settings", "pet-settings"],
    queryFn: getPetSettings,
  });
  const settingsMutation = useMutation({
    mutationFn: updatePetSettings,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "pet-settings"] });
    },
  });

  const equipMutation = useMutation({
    mutationFn: equipPet,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["settings", "pet-active"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "pets-owned"] });
    },
  });

  const pullMutation = useMutation({
    mutationFn: pullPet,
    onSuccess: (outcome) => {
      setPullMessage(
        t("pullResult", { pet: petName(catalogQuery.data ?? [], outcome.pet_id, locale) }),
      );
      void queryClient.invalidateQueries({ queryKey: ["settings", "pets-owned"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "pet-progress"] });
      void queryClient.invalidateQueries({ queryKey: ["settings", "pet-active"] });
    },
    onError: (err) => {
      setPullMessage(
        err instanceof SettingsApiError && err.code === "no_pulls_available"
          ? t("noPullsAvailable")
          : tc("errorGeneric"),
      );
    },
  });

  const loading =
    catalogQuery.isLoading ||
    ownedQuery.isLoading ||
    activeQuery.isLoading ||
    progressQuery.isLoading;
  const anyError =
    catalogQuery.isError || ownedQuery.isError || activeQuery.isError || progressQuery.isError;

  if (loading) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={4} />
      </SettingsSection>
    );
  }

  if (anyError) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsError
          message={tc("errorLoad")}
          onRetry={() => {
            void catalogQuery.refetch();
            void ownedQuery.refetch();
            void activeQuery.refetch();
            void progressQuery.refetch();
          }}
        />
      </SettingsSection>
    );
  }

  const ownedByPetId = new Set((ownedQuery.data ?? []).map((p) => p.pet_id));
  const activeRecordId = activeQuery.data?.id ?? null;
  const progress = progressQuery.data;
  const canPull = (progress?.available_pulls ?? 0) > 0;

  return (
    <>
      <SettingsSection title={t("progressTitle")} className="mb-6">
        <div className="grid grid-cols-2 gap-4 py-1 text-sm sm:grid-cols-4">
          <Stat label={t("availablePulls")} value={progress?.available_pulls ?? 0} />
          <Stat label={t("streakDays")} value={progress?.streak_days ?? 0} />
          <Stat
            label={t("owned")}
            value={`${progress?.owned_count ?? 0}/${progress?.catalog_size ?? 0}`}
          />
          <Stat label={t("pityCounter")} value={progress?.pity_counter ?? 0} />
        </div>
        <div className="flex items-center justify-between gap-3 pt-4">
          <p className="text-[12px] text-[var(--text-muted)]">{t("pullHint")}</p>
          <Button
            variant="filled"
            size="sm"
            disabled={!canPull || pullMutation.isPending}
            onClick={() => pullMutation.mutate()}
          >
            {t("pull")}
          </Button>
        </div>
        {pullMessage ? (
          <p role="status" aria-live="polite" className="pt-2 text-[13px] text-[var(--text-muted)]">
            {pullMessage}
          </p>
        ) : null}
      </SettingsSection>

      <SettingsSection title={t("settingsTitle")} description={t("settingsHint")} className="mb-6">
        <SettingsRow label={t("showCompanion")} hint={t("showCompanionHint")}>
          <ToggleSwitch
            checked={settingsQuery.data?.companion_visible ?? true}
            onChange={(next) => settingsMutation.mutate({ companion_visible: next })}
            label={t("showCompanion")}
            disabled={settingsQuery.isLoading || settingsMutation.isPending}
          />
        </SettingsRow>
        <SettingsRow label={t("flavourReplies")} hint={t("flavourRepliesHint")}>
          <ToggleSwitch
            checked={settingsQuery.data?.hint_enabled ?? false}
            onChange={(next) => settingsMutation.mutate({ hint_enabled: next })}
            label={t("flavourReplies")}
            disabled={settingsQuery.isLoading || settingsMutation.isPending}
          />
        </SettingsRow>
      </SettingsSection>

      <SettingsSection title={t("collectionTitle")}>
        <div className="grid grid-cols-3 gap-3 py-1 sm:grid-cols-4">
          {(catalogQuery.data ?? []).map((entry: PetCatalogEntry) => {
            const owned = ownedByPetId.has(entry.pet_id);
            const ownedRecord = (ownedQuery.data ?? []).find((p) => p.pet_id === entry.pet_id);
            const isActive = ownedRecord?.id === activeRecordId;
            return (
              <div
                key={entry.pet_id}
                className={cn(
                  "flex flex-col items-center gap-1 rounded-[var(--radius-input)] border p-3 text-center",
                  owned
                    ? "border-[var(--border)] bg-[var(--surface)]"
                    : "border-[var(--border)] bg-[var(--surface-2)] opacity-50",
                )}
              >
                <span className={cn("font-medium text-[13px]", RARITY_TEXT[entry.rarity])}>
                  {entry.name_zh}
                </span>
                <span className="text-[11px] text-[var(--text-muted)]">
                  {t(`rarity.${entry.rarity}`)}
                </span>
                {owned && ownedRecord && progress ? (
                  <OwnedLevel level={ownedRecord.level} xp={ownedRecord.xp} progress={progress} />
                ) : null}
                {owned && ownedRecord ? (
                  <Button
                    variant={isActive ? "outline" : "ghost"}
                    size="sm"
                    disabled={isActive || equipMutation.isPending}
                    onClick={() => equipMutation.mutate(ownedRecord.id)}
                    className="mt-1 px-2 text-[12px]"
                  >
                    {isActive ? t("equipped") : t("equip")}
                  </Button>
                ) : (
                  <span className="mt-1 text-[11px] text-[var(--text-muted)]">{t("locked")}</span>
                )}
              </div>
            );
          })}
        </div>
      </SettingsSection>
    </>
  );
}

function Stat({ label, value }: { label: string; value: string | number }): ReactNode {
  return (
    <div>
      <p className="font-num text-[20px] text-[var(--text)] tabular-nums">{value}</p>
      <p className="text-[12px] text-[var(--text-muted)]">{label}</p>
    </div>
  );
}

function OwnedLevel({
  level,
  xp,
  progress,
}: {
  level: number;
  xp: number;
  progress: PetProgress;
}): ReactNode {
  const t = useTranslations("settings.pets");
  const percent = Math.round(xpFraction(level, xp, progress) * 100);
  return (
    <div className="w-full">
      <span className="font-num text-[11px] text-[var(--text-muted)]">{t("level", { level })}</span>
      <div
        role="progressbar"
        aria-label={t("xp")}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        className="mx-auto mt-1 h-1 w-full overflow-hidden rounded-full bg-[var(--surface-2)]"
      >
        <div className="h-full rounded-full bg-[var(--accent)]" style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
