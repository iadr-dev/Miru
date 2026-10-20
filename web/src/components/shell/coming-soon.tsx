import type { Icon } from "@phosphor-icons/react";
import type { ReactNode } from "react";

/** Honest placeholder for a nav destination that isn't built yet (Stock / Screener / Settings). No
 * fake functionality — just a calm labelled panel inside the real shell. RSC (no interactivity). */
export function ComingSoon({
  icon: Icon,
  title,
  body,
}: Readonly<{
  icon: Icon;
  title: string;
  body: string;
}>): ReactNode {
  return (
    <div className="flex min-h-full items-center justify-center p-6">
      <div className="surface-card w-full max-w-lg p-8 text-center">
        <div className="mx-auto grid size-12 place-items-center rounded-[var(--radius-button)] bg-[var(--surface-2)]">
          <Icon size={24} weight="regular" className="text-[var(--text-muted)]" />
        </div>
        <h1 className="mt-4 font-semibold text-[var(--text)] text-xl">{title}</h1>
        <p className="mt-2 text-[var(--text-muted)] text-sm">{body}</p>
      </div>
    </div>
  );
}
