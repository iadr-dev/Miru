import type { ReactNode } from "react";

/** A native `<select>` "spinner" for a chart's lookback-window control (3M/6M/1Y) — styled to
 * the design tokens rather than the browser default chrome. Chosen over a pill-tab row per this
 * session's explicit direction: a range picker with 3+ options reads better as one compact
 * dropdown than a growing row of tabs. */
export function RangeSelect<T extends string>({
  value,
  onChange,
  options,
  labelFor,
  ariaLabel,
}: {
  readonly value: T;
  readonly onChange: (value: T) => void;
  readonly options: readonly T[];
  readonly labelFor: (option: T) => string;
  readonly ariaLabel: string;
}): ReactNode {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as T)}
      aria-label={ariaLabel}
      className="min-h-8 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)] px-2 text-[11px] text-[var(--text)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {labelFor(option)}
        </option>
      ))}
    </select>
  );
}
