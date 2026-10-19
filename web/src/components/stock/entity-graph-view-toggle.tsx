"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export type GraphView = "flat" | "sphere";

const VIEWS = ["flat", "sphere"] as const satisfies readonly GraphView[];

/** The 平面 / 立體 switch. A tab strip, so it follows the app's one tab behaviour
 * (web/OVERVIEW.md §7): roving tab stop, arrows move, Enter/Space activates. */
export function EntityGraphViewToggle({
  view,
  onChange,
}: {
  readonly view: GraphView;
  readonly onChange: (view: GraphView) => void;
}): ReactNode {
  const t = useTranslations("stock.industry.entityGraph");
  const labels: Record<GraphView, string> = { flat: t("viewFlat"), sphere: t("viewSphere") };
  return (
    <div
      role="tablist"
      aria-label={t("viewLabel")}
      className="inline-flex self-end rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] p-0.5"
    >
      {VIEWS.map((id) => {
        const selected = id === view;
        return (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={selected}
            onClick={() => onChange(id)}
            className={cn(
              "min-h-11 min-w-16 rounded-[var(--radius-pill)] px-4 text-[13px] transition-colors focus-visible:outline-2 focus-visible:outline-[var(--accent)] active:scale-[0.98]",
              selected
                ? "bg-[var(--accent-weak)] font-medium text-[var(--text)]"
                : "text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {labels[id]}
          </button>
        );
      })}
    </div>
  );
}
