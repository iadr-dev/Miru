"use client";

import { ArrowUp, LockSimple } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import { type FormEvent, type ReactNode, useState } from "react";
import { cn } from "@/lib/cn";

type ComposerProps = {
  /** Called when an anonymous user tries to send — the entry composer is a sign-in come-on because
   * /agent/query requires auth (backend require_user). We pass the drafted text up so it can be
   * preserved through the sign-in round trip. */
  onAttemptSend: (draft: string) => void;
};

/** The entry composer. It is deliberately NOT a dead input: anonymous users can type, but sending
 * prompts sign-in (the backend requires auth for a turn). We don't build a lie into the UI — the
 * send affordance clearly reads as a sign-in gate via its label + lock hint. */
export function Composer({ onAttemptSend }: ComposerProps): ReactNode {
  const t = useTranslations("hero.composer");
  const [draft, setDraft] = useState("");

  const submit = (e: FormEvent): void => {
    e.preventDefault();
    onAttemptSend(draft.trim());
  };

  return (
    <form onSubmit={submit} className="w-full">
      <div
        className={cn(
          "flex items-end gap-2 rounded-[var(--radius-card)] border border-[var(--border)]",
          "bg-[var(--surface)] p-2 shadow-lg transition-colors focus-within:border-[var(--accent)]",
        )}
      >
        <label htmlFor="miru-composer" className="sr-only">
          {t("label")}
        </label>
        <textarea
          id="miru-composer"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              onAttemptSend(draft.trim());
            }
          }}
          rows={1}
          placeholder={t("placeholder")}
          className={cn(
            "max-h-40 min-h-11 flex-1 resize-none bg-transparent px-3 py-2.5",
            "text-[15px] text-[var(--text)] leading-relaxed placeholder:text-[var(--text-muted)]",
            "outline-none",
          )}
        />
        <button
          type="submit"
          aria-label={t("signInToSend")}
          title={t("signInToSend")}
          className={cn(
            "inline-flex size-11 shrink-0 items-center justify-center rounded-[var(--radius-button)]",
            "bg-[var(--accent)] text-[var(--bg)] transition-opacity hover:opacity-90 active:scale-95",
          )}
        >
          <ArrowUp size={20} weight="bold" />
        </button>
      </div>
      <p className="mt-2 flex items-center justify-center gap-1.5 text-[13px] text-[var(--text-muted)]">
        <LockSimple size={13} weight="regular" aria-hidden />
        {t("signInToSend")}
      </p>
    </form>
  );
}
