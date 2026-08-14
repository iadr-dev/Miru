import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Grouped-row section card shared by every Settings sub-page (docs/web/settings/README.md
 * §Visual design): `--radius-card` surface, 18–20px/600 section head, `divide-y --border` rows. */
export function SettingsSection({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}): ReactNode {
  return (
    <section
      className={cn(
        "rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5",
        className,
      )}
    >
      <h2 className="font-semibold text-[18px] text-[var(--text)]">{title}</h2>
      {description ? (
        <p className="mt-1 text-[13px] text-[var(--text-muted)] leading-relaxed">{description}</p>
      ) : null}
      <div className="mt-4 divide-y divide-[var(--border)]">{children}</div>
    </section>
  );
}

/** A single labelled row inside a `SettingsSection` (label + control, stacked on mobile). */
export function SettingsRow({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}): ReactNode {
  return (
    <div className="flex flex-col gap-2 py-4 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
      <div className="min-w-0">
        <p className="font-medium text-[14px] text-[var(--text)]">{label}</p>
        {hint ? <p className="mt-0.5 text-[12px] text-[var(--text-muted)]">{hint}</p> : null}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}
