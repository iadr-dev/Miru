"use client";

import { BrainIcon, CaretDownIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Link } from "@/i18n/navigation";
import type { MemoryRef } from "@/types/usage";

/** "Used N memories" under an answer: which long-term memories informed it, so the user can see
 * (and manage) what Miru knew. Renders nothing for a turn that used none, and starts collapsed so
 * it stays a quiet line, not a block. */
export function MemoryUsedChip({ memories }: Readonly<{ memories: MemoryRef[] }>): ReactNode {
  const t = useTranslations("chat.memoryUsed");
  const tm = useTranslations("settings.memory");
  const [open, setOpen] = useState(false);
  if (memories.length === 0) {
    return null;
  }
  return (
    <div className="mt-2 text-[12px] text-[var(--text-muted)]">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="!outline-none inline-flex min-h-8 items-center gap-1.5 rounded-[var(--radius-button)] hover:text-[var(--text)] focus-visible:ring-2 focus-visible:ring-[var(--accent)]"
      >
        <BrainIcon size={14} weight="regular" aria-hidden />
        <span>{t("summary", { count: memories.length })}</span>
        <CaretDownIcon
          size={12}
          weight="regular"
          aria-hidden
          className={open ? "rotate-180" : undefined}
        />
      </button>
      {open ? (
        <ul className="m-0 mt-1 flex list-none flex-col gap-1 p-0">
          {memories.map((memory) => (
            <li key={memory.id} className="leading-relaxed">
              <span className="text-[var(--text)]">{memory.text || memory.key}</span>
              <span> · {tm(`typeOption.${memory.type}`)}</span>
            </li>
          ))}
          <li>
            <Link
              href="/settings/memory"
              className="text-[var(--accent-text)] underline-offset-2 hover:underline"
            >
              {t("manage")}
            </Link>
          </li>
        </ul>
      ) : null}
    </div>
  );
}
