"use client";

import { XIcon } from "@phosphor-icons/react/dist/ssr";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { FilterValueInput } from "@/components/screener/filter-value-input";
import type { FactorField, ScreenerOperator, ScreenerRunCondition } from "@/types/screener";

/** factor · operator · value, in a `--surface-2` card (README.md §4 `ConditionRow`). Sponsor
 * factors render a 🔒 chip in place of the value control — the value is never resolved
 * client-side for an unentitled caller, matching the backend's own "listed but blurred" rule
 * (`factor_catalog.py`'s sponsor factors never compute a value for this pass). */
export function ConditionRow({
  field,
  condition,
  onOperatorChange,
  onValueChange,
  onRemove,
}: {
  readonly field: FactorField | undefined;
  readonly condition: ScreenerRunCondition;
  readonly onOperatorChange: (op: ScreenerOperator) => void;
  readonly onValueChange: (value: number | string) => void;
  readonly onRemove: () => void;
}): ReactNode {
  const tOperators = useTranslations("screener.operators");
  if (!field) return null;

  return (
    <div className="flex flex-col gap-2 rounded-[var(--radius-input)] bg-[var(--surface-2)] p-3">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text)]">
          {field.label_zh}
        </span>
        <select
          name={`${field.key}_operator`}
          aria-label={`${field.label_zh} operator`}
          value={condition.op}
          onChange={(event) => onOperatorChange(event.target.value as ScreenerOperator)}
          className="min-h-9 shrink-0 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        >
          {field.op_set.map((op) => (
            <option key={op} value={op}>
              {tOperators(op)}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={onRemove}
          aria-label="remove"
          className="grid size-9 shrink-0 place-items-center rounded-[var(--radius-button)] text-[var(--text-muted)] transition-colors hover:text-[var(--down)]"
        >
          <XIcon size={16} weight="regular" />
        </button>
      </div>
      {field.data_class === "sponsor" ? (
        <span className="text-[13px] text-[var(--text-muted)]">🔒 sponsor</span>
      ) : (
        <FilterValueInput field={field} value={condition.value} onChange={onValueChange} />
      )}
    </div>
  );
}
