"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/cn";
import type { FactorField } from "@/types/screener";

/** Enum-like factors (`symbol_type`, `distribution_frequency`) get a plain `<select>` over
 * `field.enum_values` instead of the numeric slider below — those factors only support `eq`
 * (`factor_catalog.py`'s `op_set` for them), so a dropdown is the whole control, no operator-
 * dependent layout to branch on. */
function EnumValueInput({
  field,
  value,
  onChange,
}: {
  readonly field: FactorField;
  readonly value: string | undefined;
  readonly onChange: (next: string) => void;
}): ReactNode {
  const options = field.enum_values ?? [];
  const resolved = value ?? options[0] ?? "";

  return (
    <select
      name={`${field.key}_enum`}
      aria-label={field.label_zh}
      value={resolved}
      onChange={(event) => onChange(event.target.value)}
      className="min-h-11 w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

/** The "1B problem" fix (README.md §4): a labeled slider bounded by the field's `[min,max]/step`
 * + min/max tick labels + one-click preset chips + an exact-value box — reports the raw number
 * the API expects while showing human units. `between` is available as an operator but not wired
 * to a dual-thumb range control — every current numeric factor is queried with a single-value
 * operator in practice. Enum-like factors (`field.enum_values` set) delegate to `EnumValueInput`
 * above instead of rendering a meaningless slider. */
export function FilterValueInput({
  field,
  value,
  onChange,
}: {
  readonly field: FactorField;
  readonly value: number | string | undefined;
  readonly onChange: (next: number | string) => void;
}): ReactNode {
  if (field.enum_values && field.enum_values.length > 0) {
    return (
      <EnumValueInput
        field={field}
        value={typeof value === "string" ? value : undefined}
        onChange={onChange}
      />
    );
  }

  const min = field.min ?? 0;
  const max = field.max ?? 100;
  const step = field.step ?? 1;
  const resolved = typeof value === "number" ? value : min;

  return (
    <div className="flex flex-1 flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <input
          type="range"
          name={`${field.key}_range`}
          min={min}
          max={max}
          step={step}
          value={resolved}
          onChange={(event) => onChange(Number(event.target.value))}
          className="min-h-11 flex-1 accent-[var(--accent)]"
          aria-label={field.label_zh}
        />
        <input
          type="number"
          name={`${field.key}_exact`}
          min={min}
          max={max}
          step={step}
          value={resolved}
          onChange={(event) => onChange(Number(event.target.value))}
          aria-label={`${field.label_zh} exact value`}
          className="min-h-11 w-20 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm tabular-nums focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        />
        {field.unit ? (
          <span className="shrink-0 text-[12px] text-[var(--text-muted)]">{field.unit}</span>
        ) : null}
      </div>
      <div className="flex items-center justify-between px-0.5 font-num text-[11px] text-[var(--text-muted)] tabular-nums">
        <span>
          {min}
          {field.unit}
        </span>
        <span>
          {max}
          {field.unit}
        </span>
      </div>
      {field.preset_chips.length > 0 ? (
        <div className="flex flex-wrap gap-1.5">
          {field.preset_chips.map((chip) => (
            <button
              key={chip}
              type="button"
              onClick={() => onChange(chip)}
              className={cn(
                "min-h-8 rounded-[var(--radius-pill)] px-2 font-num text-[12px] tabular-nums transition-colors",
                resolved === chip
                  ? "bg-[var(--accent-weak)] text-[var(--accent)]"
                  : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {field.op_set[0] === "gt" || field.op_set[0] === "gte" ? ">" : ""}
              {chip}
              {field.unit}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
