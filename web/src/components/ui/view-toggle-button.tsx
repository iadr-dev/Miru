"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** One pill button in a list/calendar (or similar 2-3-way) view-switcher — pulled out of
 * `basic-info-panel.tsx`'s `AnnouncementsCard` once a second caller (the ETF 總覽 tab's own
 * dividend-calendar card) needed the identical markup, per this project's own "duplicated markup
 * → one shared component" convention (see `month-grid-calendar.tsx`'s own extraction history). */
export function ViewToggleButton({
  active,
  label,
  onClick,
}: {
  readonly active: boolean;
  readonly label: string;
  readonly onClick: () => void;
}): ReactNode {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px] transition-colors",
        active
          ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
          : "text-[var(--text-muted)] hover:text-[var(--text)]",
      )}
    >
      {label}
    </button>
  );
}
