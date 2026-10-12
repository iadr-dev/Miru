"use client";

import { CaretDownIcon, CheckIcon, LockIcon } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  MODEL_CATEGORIES,
  type ModelOption,
  modelProvider,
} from "@/app/[locale]/(app)/chat/composer-parts/model-options";
import { useRouter } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/** The model-selector dropdown trigger + menu. Controlled by the caller (see `Composer`'s
 * docstring) — `selected` is derived from the thread's real `model_override`, and `onSelect`
 * reports the chosen catalog id upward for the caller to persist via `patchThread`. */
export function ModelMenu({
  label,
  selected,
  allowedProviders,
  platformModels,
  onSelect,
}: {
  label: string;
  selected: ModelOption | null;
  allowedProviders: string[] | null;
  platformModels: string[] | null;
  onSelect: (modelId: string) => void;
}): ReactNode {
  const t = useTranslations("chat.composer");
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const selectedLabel = selected?.label ?? t("modelMenu.default");
  // A caller with their own keys can only run those providers' models.
  const visibleCategories = MODEL_CATEGORIES.map((category) => ({
    ...category,
    models:
      allowedProviders === null
        ? category.models
        : category.models.filter((m) => allowedProviders.includes(modelProvider(m.id))),
  })).filter((category) => category.models.length > 0);

  return (
    <DropdownMenu.Root open={open} onOpenChange={setOpen}>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={label}
          className={cn(
            "inline-flex h-8 min-w-0 max-w-full items-center gap-1 rounded-[var(--radius-button)] px-2.5",
            "text-[12px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          )}
        >
          <span className="truncate">{t("modelMenu.trigger", { model: selectedLabel })}</span>
          <CaretDownIcon size={12} weight="regular" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={8}
          className={cn(
            "z-50 w-56 rounded-[var(--radius-card)] border border-[var(--border)]",
            "max-h-[360px] overflow-y-auto bg-[var(--surface)] p-1.5 shadow-2xl",
          )}
        >
          {visibleCategories.map((category, index) => (
            <div key={category.key}>
              {index > 0 ? (
                <DropdownMenu.Separator className="my-1 h-px bg-[var(--border)]" />
              ) : null}
              <DropdownMenu.Label className="px-2.5 py-1 text-[11px] text-[var(--text-muted)]">
                {t(`modelMenu.category.${category.key}`)}
              </DropdownMenu.Label>
              {category.models.map((model) => {
                const locked = platformModels !== null && !platformModels.includes(model.id);
                return (
                  <DropdownMenu.Item
                    key={model.id}
                    onSelect={() =>
                      locked ? router.push("/settings/billing") : onSelect(model.id)
                    }
                    className={cn(
                      "flex min-h-9 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
                      "text-sm outline-none transition-colors",
                      locked ? "text-[var(--text-muted)]" : "text-[var(--text)]",
                      "data-[highlighted]:bg-[var(--surface-2)]",
                    )}
                  >
                    <span className="flex-1">{model.label}</span>
                    {locked ? (
                      <LockIcon
                        size={14}
                        weight="regular"
                        aria-label={t("modelMenu.locked")}
                        className="text-[var(--text-muted)]"
                      />
                    ) : null}
                    {model.id === selected?.id ? (
                      <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" />
                    ) : null}
                  </DropdownMenu.Item>
                );
              })}
            </div>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
