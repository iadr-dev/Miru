"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { SettingsSection } from "@/components/settings/settings-section";
import { SavedToast, SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { fetchMe } from "@/lib/api/account";
import { listPersonalityPresets, updateProfile } from "@/lib/api/settings";
import { cn } from "@/lib/cn";

/** Miru 的語氣 preset picker - radio-card row, selected = `--accent` hairline + tint. Each preset's
 * name and description are translated per locale (`settings.personality.preset.<id>`); a preset the
 * messages don't know yet falls back to the API's own `name_zh`/`name_en`/`description`. */
export function PersonalityPanel(): ReactNode {
  const t = useTranslations("settings.personality");
  const tc = useTranslations("settings.common");
  const locale = useLocale();
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  const presetsQuery = useQuery({
    queryKey: ["settings", "personality-presets"],
    queryFn: listPersonalityPresets,
  });
  const meQuery = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => fetchMe(signal) });

  const mutation = useMutation({
    mutationFn: (personality_id: string) => updateProfile({ personality_id }),
    onMutate: () => setSaved(false),
    onSuccess: (data) => {
      queryClient.setQueryData(["me"], { user: data.user, entitlements: data.entitlements });
      setSaved(true);
      window.setTimeout(() => setSaved(false), 2500);
    },
  });

  if (presetsQuery.isLoading || meQuery.isLoading) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsLoading rows={4} />
      </SettingsSection>
    );
  }

  if (presetsQuery.isError || meQuery.isError) {
    return (
      <SettingsSection title={t("title")}>
        <SettingsError
          message={tc("errorLoad")}
          onRetry={() => {
            void presetsQuery.refetch();
            void meQuery.refetch();
          }}
        />
      </SettingsSection>
    );
  }

  const active = meQuery.data?.user.personality_id;

  return (
    <SettingsSection title={t("title")} description={t("description")}>
      <fieldset className="m-0 grid min-w-0 gap-3 border-0 p-0 py-1 sm:grid-cols-2">
        <legend className="sr-only">{t("title")}</legend>
        {(presetsQuery.data ?? []).map((preset) => {
          const selected = preset.id === active;
          return (
            <button
              key={preset.id}
              type="button"
              disabled={mutation.isPending}
              onClick={() => mutation.mutate(preset.id)}
              aria-pressed={selected}
              className={cn(
                "flex min-h-11 flex-col gap-1 rounded-[var(--radius-input)] border p-3 text-left transition-colors",
                selected
                  ? "border-[var(--accent)] bg-[var(--accent-weak)]"
                  : "border-[var(--border)] bg-[var(--surface)] hover:border-[var(--text-muted)]",
              )}
            >
              <span className="font-medium text-[14px] text-[var(--text)]">
                {t.has(`preset.${preset.id}.name`)
                  ? t(`preset.${preset.id}.name`)
                  : localizedName(preset, locale)}
              </span>
              <span className="mt-1 text-[13px] text-[var(--text-muted)] leading-relaxed">
                {t.has(`preset.${preset.id}.description`)
                  ? t(`preset.${preset.id}.description`)
                  : preset.description}
              </span>
            </button>
          );
        })}
      </fieldset>
      {mutation.isError ? (
        <p role="alert" className="pt-2 text-[13px] text-[var(--warn)]">
          {tc("errorGeneric")}
        </p>
      ) : null}
      {saved ? <SavedToast message={tc("saved")} /> : null}
    </SettingsSection>
  );
}

function localizedName(preset: { name_zh: string; name_en: string }, locale: string): string {
  return locale === "zh-TW" ? preset.name_zh : preset.name_en;
}
