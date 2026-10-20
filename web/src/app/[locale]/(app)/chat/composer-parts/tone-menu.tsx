"use client";

import { CaretDownIcon, CheckIcon } from "@phosphor-icons/react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import {
  FOLLOW_ACCOUNT_TONE,
  isPersonalityPresetId,
  PERSONALITY_PRESET_IDS,
} from "@/lib/personality";

/** The per-chat 語氣 picker: one of the presets, or "follow account setting" (the default, which
 * clears the chat's own pick). Controlled like `ModelMenu` - the caller persists the choice. */
export function ToneMenu({
  selected,
  onSelect,
}: Readonly<{
  selected: string | null;
  onSelect: (presetId: string) => void;
}>): ReactNode {
  const t = useTranslations("chat.composer.toneMenu");
  const tp = useTranslations("settings.personality.preset");
  const current = selected !== null && isPersonalityPresetId(selected) ? selected : null;
  const currentName = current === null ? t("follow") : tp(`${current}.name`);
  const rows: { id: string; label: string; active: boolean }[] = [
    { id: FOLLOW_ACCOUNT_TONE, label: t("follow"), active: current === null },
    ...PERSONALITY_PRESET_IDS.map((id) => ({
      id,
      label: tp(`${id}.name`),
      active: current === id,
    })),
  ];

  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button
          type="button"
          aria-label={t("heading")}
          className={cn(
            "inline-flex h-8 min-w-0 max-w-full items-center gap-1 rounded-[var(--radius-button)] px-2.5",
            "text-[12px] text-[var(--text-muted)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text)]",
            "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
          )}
        >
          <span className="truncate">{t("trigger", { name: currentName })}</span>
          <CaretDownIcon size={12} weight="regular" aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content
          side="top"
          align="start"
          sideOffset={8}
          className="z-50 w-56 rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-1.5 shadow-2xl"
        >
          <DropdownMenu.Label className="px-2.5 py-1 text-[11px] text-[var(--text-muted)]">
            {t("heading")}
          </DropdownMenu.Label>
          {rows.map((row) => (
            <DropdownMenu.Item
              key={row.id}
              onSelect={() => onSelect(row.id)}
              className={cn(
                "flex min-h-9 cursor-pointer items-center gap-2 rounded-[var(--radius-button)] px-2.5",
                "text-[var(--text)] text-sm outline-none transition-colors",
                "data-[highlighted]:bg-[var(--surface-2)]",
              )}
            >
              <span className="flex-1">{row.label}</span>
              {row.active ? (
                <CheckIcon size={14} weight="regular" className="text-[var(--accent)]" />
              ) : null}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
