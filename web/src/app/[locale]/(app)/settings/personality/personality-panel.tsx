"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { SettingsSection } from "@/components/settings/settings-section";
import { SavedToast, SettingsError, SettingsLoading } from "@/components/settings/settings-states";
import { fetchMe } from "@/lib/api/account";
import { listPersonalityPresets, updateProfile } from "@/lib/api/settings";
import { cn } from "@/lib/cn";

/** 了然的語氣 preset picker — radio-card row, selected = `--accent` hairline + tint
 * (docs/web/settings/README.md §Visual design). Labels come from the API (`name_zh`/`name_en`),
 * never hardcoded guesses. */
export function PersonalityPanel(): ReactNode {
  const t = useTranslations("settings.personality");
  const tc = useTranslations("settings.common");
  const queryClient = useQueryClient();
  const [saved, setSaved] = useState(false);

  const presetsQuery = useQuery({
    queryKey: ["settings", "personality-presets"],
    queryFn: listPersonalityPresets,
  });
  const meQuery = useQuery({ queryKey: ["me"], queryFn: ({ signal }) => fetchMe(signal) });

  const mutation = useMutation({
    mutationFn: (personality_id: string) => updateProfile({ personality_id }),
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
      <div className="grid gap-3 py-1 sm:grid-cols-2">
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
              <span className="font-medium text-[14px] text-[var(--text)]">{preset.name_zh}</span>
              <span className="text-[12px] text-[var(--text-muted)]">{preset.name_en}</span>
              <span className="mt-1 text-[13px] text-[var(--text-muted)] leading-relaxed">
                {preset.description}
              </span>
            </button>
          );
        })}
      </div>
      {saved ? <SavedToast message={tc("saved")} /> : null}
    </SettingsSection>
  );
}
