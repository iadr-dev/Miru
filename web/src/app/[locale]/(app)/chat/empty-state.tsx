"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Chat cold-start empty state (interaction-spec §4.2 cold-start class): "問我任何台股問題" heading +
 * prompt-starter chips. Clicking a chip sends it as the first turn. This is the cold-start class
 * (user has no active conversation), so routing INTO chat here is correct.
 */
export function EmptyState({ onPick }: { onPick: (prompt: string) => void }): ReactNode {
  const t = useTranslations("chat.empty");
  const starters = [t("starters.s1"), t("starters.s2"), t("starters.s3")];

  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-10 text-center">
      <h2 className="font-semibold text-2xl text-[var(--text)] tracking-tight">{t("heading")}</h2>
      <p className="mt-3 max-w-md text-[15px] text-[var(--text-muted)] leading-relaxed">
        {t("subheading")}
      </p>
      <div className="mt-8 flex w-full max-w-2xl flex-wrap justify-center gap-2">
        {starters.map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => onPick(prompt)}
            className={cn(
              "min-h-11 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] px-4",
              "text-left text-[var(--text)] text-sm transition-colors",
              "hover:border-[var(--accent)]",
            )}
          >
            {prompt}
          </button>
        ))}
      </div>
    </div>
  );
}
