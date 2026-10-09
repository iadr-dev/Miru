"use client";

import { SmileyIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { isPersonalityPresetId } from "@/lib/personality";

/** "語氣：數據派" under an answer: which tone preset styled it, so the user can tell what they got
 * (and why the format looks the way it does). Renders nothing when the turn recorded none. */
export function PersonalityChip({ presetId }: { presetId: string | null }): ReactNode {
  const t = useTranslations("chat.personalityUsed");
  const tp = useTranslations("settings.personality.preset");
  if (presetId === null) {
    return null;
  }
  const name = isPersonalityPresetId(presetId) ? tp(`${presetId}.name`) : presetId;
  return (
    <p className="mt-2 flex min-h-8 items-center gap-1.5 text-[12px] text-[var(--text-muted)]">
      <SmileyIcon size={14} weight="regular" aria-hidden />
      <span>{t("summary", { name })}</span>
    </p>
  );
}
