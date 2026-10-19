"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Select } from "@/components/settings/form-controls";
import { SettingsRow } from "@/components/settings/settings-section";
import { QueryFailure } from "@/components/ui/query-state";
import { Skeleton } from "@/components/ui/skeleton";
import { useByokActive, useModelDefault, useModels, useSetModelDefault } from "@/hooks/use-models";
import { groupByProvider, labelForModelId, modelLabel } from "@/lib/model-catalog";

/** The default model for a caller on their own keys: the model their chats and scheduled runs
 * use when nothing else is picked (a chat's own pick still wins). "Automatic" clears it. Anyone
 * else gets a short neutral note, because the platform chooses the model for them. */
export function DefaultModelSetting(): ReactNode {
  const t = useTranslations("settings.byok.defaultModel");
  const tProvider = useTranslations("settings.byok.providerOption");
  const locale = useLocale();
  const byokActive = useByokActive();
  const catalog = useModels(byokActive === true);
  const saved = useModelDefault(byokActive === true);
  const setDefault = useSetModelDefault();

  if (byokActive === undefined) {
    return <Skeleton className="h-11 w-full" />;
  }
  if (!byokActive) {
    return (
      <p className="text-[12px] text-[var(--text-muted)] leading-relaxed">{t("platformNote")}</p>
    );
  }
  if (catalog.isError) {
    return <QueryFailure query={catalog} />;
  }
  if (saved.isError) {
    return <QueryFailure query={saved} />;
  }
  if (catalog.isLoading || saved.isLoading) {
    return <Skeleton className="h-11 w-full" />;
  }

  const models = catalog.data?.models ?? [];
  if (models.length === 0) {
    return <p className="text-[12px] text-[var(--text-muted)] leading-relaxed">{t("empty")}</p>;
  }

  const current = saved.data?.default_model ?? null;
  const listed = current !== null && models.some((m) => m.id === current);

  return (
    <SettingsRow label={t("label")} hint={t("hint")}>
      <Select
        aria-label={t("label")}
        value={current ?? ""}
        onChange={(e) => setDefault.mutate(e.target.value === "" ? null : e.target.value)}
        className="w-full sm:w-64"
      >
        <option value="">{t("auto")}</option>
        {current !== null && !listed ? (
          <option value={current}>{labelForModelId(current, models, locale)}</option>
        ) : null}
        {groupByProvider(models).map((group) => (
          <optgroup key={group.provider} label={tProvider(group.provider)}>
            {group.models.map((model) => (
              <option key={model.id} value={model.id}>
                {modelLabel(model, locale)}
              </option>
            ))}
          </optgroup>
        ))}
      </Select>
    </SettingsRow>
  );
}
