"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** A small single-choice chip row (range 1Y/3Y/5Y, units selector). Plain toggle buttons with
 * `aria-pressed` inside a labelled group; each chip keeps the 44px touch target. */
export function ChipGroup<Value extends string>({
  options,
  value,
  onChange,
  ariaLabel,
}: {
  readonly options: readonly { readonly value: Value; readonly label: string }[];
  readonly value: Value;
  readonly onChange: (next: Value) => void;
  readonly ariaLabel: string;
}): ReactNode {
  return (
    <div role="group" aria-label={ariaLabel} className="flex flex-wrap gap-1.5">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={cn(
              "min-h-11 rounded-[var(--radius-pill)] px-3.5 text-[12.5px] transition-colors active:scale-[0.98]",
              "focus-visible:outline-2 focus-visible:outline-[var(--accent)] focus-visible:outline-offset-2",
              active
                ? "bg-[var(--accent-weak)] font-semibold text-[var(--accent-text)]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
