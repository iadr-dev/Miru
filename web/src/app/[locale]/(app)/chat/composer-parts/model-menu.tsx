"use client";

import { CaretDownIcon, CheckIcon } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { useModels } from "@/hooks/use-models";
import { cn } from "@/lib/cn";
import { defaultModel, groupByProvider, labelForModelId, modelLabel } from "@/lib/model-catalog";

const USE_DEFAULT = "__default__";

const ITEM_CLASS = cn(
  "flex min-h-11 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
  "text-[var(--text)] text-sm outline-none transition-colors",
  "data-[highlighted]:bg-[var(--surface-2)] data-[state=checked]:font-medium",
);

/** The model picker for a caller running on their own keys. Renders nothing for anyone else (the
 * platform chooses the model, so there is no control and no teaser). Options come from
 * `GET /models`, grouped by provider; the chat's own pick overrides the caller's saved default,
 * and "use my default" clears it. Controlled: `selectedId` is the thread's real `model_override`
 * and `onSelect(null)` asks the caller to clear it. */
export function ModelMenu({
  label,
  byokActive,
  selectedId,
  onSelect,
}: {
  label: string;
  byokActive: boolean | undefined;
  selectedId: string | null;
  onSelect: (modelId: string | null) => void;
}): ReactNode {
  const t = useTranslations("chat.composer.modelMenu");
  const tProvider = useTranslations("settings.byok.providerOption");
  const locale = useLocale();
  const catalog = useModels(byokActive === true);

  if (byokActive !== true) {
    return null;
  }

  const models = catalog.data?.models ?? [];
  const mainModel = defaultModel(models);
  const triggerModel = selectedId ?? mainModel?.id ?? null;
  const triggerLabel =
    triggerModel === null ? t("default") : labelForModelId(triggerModel, models, locale);
  const groups = groupByProvider(models);

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "inline-flex min-h-11 min-w-0 max-w-full items-center gap-1 rounded-[var(--radius-button)] px-2.5",
            "text-[12px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)] active:scale-[0.98]",
          )}
        >
          {catalog.isLoading ? (
            <span
              aria-hidden
              className="skeleton inline-block h-4 w-24 rounded-[var(--radius-input)]"
            />
          ) : (
            <span className="truncate">{t("trigger", { model: triggerLabel })}</span>
          )}
          <CaretDownIcon size={12} weight="regular" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={8}
          className={cn(
            "z-50 w-64 max-w-[calc(100vw-2rem)] rounded-[var(--radius-card)] border border-[var(--border)]",
            "max-h-[360px] overflow-y-auto bg-[var(--surface)] p-1.5 shadow-2xl",
          )}
        >
          {catalog.isLoading ? (
            <div className="space-y-2 p-2" aria-hidden>
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-2/3" />
            </div>
          ) : null}
          {catalog.isError ? (
            <div role="alert" className="p-2">
              <p className="text-[13px] text-[var(--text)]">{t("loadError")}</p>
              <button
                type="button"
                onClick={() => void catalog.refetch()}
                className="!outline-none mt-1 inline-flex min-h-11 items-center font-medium text-[13px] text-[var(--accent-text)] hover:underline focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
              >
                {t("retry")}
              </button>
            </div>
          ) : null}
          {catalog.isSuccess && models.length === 0 ? (
            <p className="p-2 text-[13px] text-[var(--text-muted)] leading-relaxed">{t("empty")}</p>
          ) : null}
          {catalog.isSuccess && models.length > 0 ? (
            <DropdownMenu.RadioGroup
              value={selectedId ?? USE_DEFAULT}
              onValueChange={(value) => onSelect(value === USE_DEFAULT ? null : value)}
            >
              <DropdownMenu.RadioItem value={USE_DEFAULT} className={ITEM_CLASS}>
                <span className="min-w-0 flex-1">
                  <span className="block">{t("useDefault")}</span>
                  {mainModel ? (
                    <span className="block truncate text-[12px] text-[var(--text-muted)]">
                      {modelLabel(mainModel, locale)}
                    </span>
                  ) : null}
                </span>
                <DropdownMenu.ItemIndicator aria-hidden className="text-[var(--accent)]">
                  <CheckIcon size={14} weight="regular" />
                </DropdownMenu.ItemIndicator>
              </DropdownMenu.RadioItem>
              {groups.map((group) => (
                <div key={group.provider}>
                  <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
                  <DropdownMenu.Label className="px-2.5 py-1 text-[11px] text-[var(--text-muted)]">
                    {tProvider(group.provider)}
                  </DropdownMenu.Label>
                  {group.models.map((model) => (
                    <DropdownMenu.RadioItem key={model.id} value={model.id} className={ITEM_CLASS}>
                      <span className="min-w-0 flex-1 truncate">{modelLabel(model, locale)}</span>
                      {model.is_default ? (
                        <span className="shrink-0 rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[11px] text-[var(--text-muted)]">
                          {t("mainBadge")}
                        </span>
                      ) : null}
                      <DropdownMenu.ItemIndicator aria-hidden className="text-[var(--accent)]">
                        <CheckIcon size={14} weight="regular" />
                      </DropdownMenu.ItemIndicator>
                    </DropdownMenu.RadioItem>
                  ))}
                </div>
              ))}
            </DropdownMenu.RadioGroup>
          ) : null}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
