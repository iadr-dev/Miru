"use client";

import { XIcon } from "@phosphor-icons/react/dist/ssr";
import * as Dialog from "@radix-ui/react-dialog";
import { motion, useReducedMotion } from "motion/react";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { FilterValueInput } from "@/components/screener/filter-value-input";
import { useRunScreener } from "@/hooks/use-screener";
import { cn } from "@/lib/cn";
import type {
  FactorCatalog,
  FactorField,
  ScreenerMarket,
  ScreenerOperator,
  ScreenerRunCondition,
} from "@/types/screener";

/** The "新增/編輯指標條件" slide-over (README.md §4's pro path, full-drawer variant): family
 * tabs down the left, the chosen factor's operator + `FilterValueInput` in the body, a live
 * survivor-count preview, and a cancel/confirm footer — the fuller condition-editing surface a
 * right-rail inline popover can't comfortably hold. Opens with an optional `initialFactorKey` to
 * edit an existing condition in place, or unset to add a new one. */
export function ConditionDrawer({
  open,
  onOpenChange,
  catalog,
  market,
  existingConditions,
  match,
  initialFactorKey,
  onConfirm,
}: {
  readonly open: boolean;
  readonly onOpenChange: (open: boolean) => void;
  readonly catalog: FactorCatalog | undefined;
  readonly market: ScreenerMarket;
  readonly existingConditions: ScreenerRunCondition[];
  readonly match: "AND" | "OR";
  readonly initialFactorKey?: string;
  readonly onConfirm: (condition: ScreenerRunCondition) => void;
}): ReactNode {
  const t = useTranslations("screener.drawer");
  const tOperators = useTranslations("screener.operators");
  const reduceMotion = useReducedMotion();

  const families = Array.from(new Set((catalog?.fields ?? []).map((field) => field.family)));
  const [activeFamily, setActiveFamily] = useState<string | null>(null);
  const [selectedFactorKey, setSelectedFactorKey] = useState<string | null>(
    initialFactorKey ?? null,
  );
  const [op, setOp] = useState<ScreenerOperator>("gte");
  const [value, setValue] = useState<number | string>(0);

  const fieldsByKey = new Map((catalog?.fields ?? []).map((field) => [field.key, field]));
  const resolvedFamily = activeFamily ?? families[0] ?? null;
  const familyFields = (catalog?.fields ?? []).filter((field) => field.family === resolvedFamily);
  const selectedField: FactorField | undefined = selectedFactorKey
    ? fieldsByKey.get(selectedFactorKey)
    : undefined;

  function selectFactor(field: FactorField): void {
    setSelectedFactorKey(field.key);
    setOp(field.op_set[0] ?? "gte");
    setValue(field.enum_values?.[0] ?? field.min ?? 0);
  }

  const previewConditions: ScreenerRunCondition[] = selectedField
    ? [...existingConditions, { factor: selectedField.key, op, value }]
    : existingConditions;

  const previewQuery = useRunScreener({
    market,
    conditions: previewConditions,
    match,
    countOnly: true,
    enabled: open && selectedField !== undefined,
  });

  function handleConfirm(): void {
    if (!selectedField) return;
    onConfirm({ factor: selectedField.key, op, value });
    onOpenChange(false);
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay asChild>
          <motion.div
            className="fixed inset-0 z-50 bg-black/50"
            initial={{ opacity: reduceMotion ? 1 : 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.15 }}
          />
        </Dialog.Overlay>
        <Dialog.Content asChild>
          <motion.div
            className="fixed inset-y-0 right-0 z-50 flex w-[min(480px,100vw)] flex-col border-[var(--border)] border-l bg-[var(--surface)] shadow-2xl"
            initial={{ x: reduceMotion ? 0 : "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", stiffness: 320, damping: 32 }}
          >
            <div className="flex items-center justify-between border-[var(--border)] border-b px-5 py-4">
              <Dialog.Title className="font-semibold text-[15px] text-[var(--text)]">
                {t("title")}
              </Dialog.Title>
              <Dialog.Close
                aria-label={t("cancel")}
                className="grid size-9 place-items-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:bg-[var(--surface-2)]"
              >
                <XIcon size={16} weight="regular" />
              </Dialog.Close>
            </div>

            <div className="flex min-h-0 flex-1 overflow-hidden">
              <div
                role="tablist"
                aria-orientation="vertical"
                className="flex w-32 shrink-0 flex-col gap-1 border-[var(--border)] border-r p-2"
              >
                {families.map((family) => (
                  <button
                    key={family}
                    type="button"
                    role="tab"
                    aria-selected={resolvedFamily === family}
                    onClick={() => setActiveFamily(family)}
                    className={cn(
                      "min-h-11 rounded-[var(--radius-input)] px-2 text-left text-[12px] transition-colors",
                      resolvedFamily === family
                        ? "bg-[var(--accent-weak)] font-medium text-[var(--accent)]"
                        : "text-[var(--text-muted)] hover:bg-[var(--surface-2)]",
                    )}
                  >
                    {family}
                  </button>
                ))}
              </div>

              <div className="flex-1 overflow-y-auto p-4">
                <p className="mb-2 text-[11px] text-[var(--text-muted)]">{t("selectFactor")}</p>
                <div className="mb-4 flex flex-col gap-1">
                  {familyFields.map((field) => (
                    <button
                      key={field.key}
                      type="button"
                      aria-pressed={selectedFactorKey === field.key}
                      onClick={() => selectFactor(field)}
                      className={cn(
                        "min-h-11 rounded-[var(--radius-input)] px-3 text-left text-[13px] transition-colors",
                        selectedFactorKey === field.key
                          ? "bg-[var(--accent-weak)] text-[var(--accent)]"
                          : "text-[var(--text)] hover:bg-[var(--surface-2)]",
                      )}
                    >
                      {field.label_zh}
                      {field.data_class === "sponsor" ? " 🔒" : ""}
                    </button>
                  ))}
                </div>

                {selectedField ? (
                  <div className="flex flex-col gap-3 rounded-[var(--radius-card)] bg-[var(--surface-2)] p-3.5">
                    <div className="flex items-center justify-between">
                      <span className="font-medium text-[13px] text-[var(--text)]">
                        {selectedField.label_zh}
                      </span>
                      <select
                        name="operator"
                        aria-label={t("selectFactor")}
                        value={op}
                        onChange={(event) => setOp(event.target.value as ScreenerOperator)}
                        className="min-h-9 rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm"
                      >
                        {selectedField.op_set.map((operator) => (
                          <option key={operator} value={operator}>
                            {tOperators(operator)}
                          </option>
                        ))}
                      </select>
                    </div>
                    {selectedField.data_class === "sponsor" ? (
                      <p className="text-[12px] text-[var(--text-muted)]">🔒 {t("sponsorNote")}</p>
                    ) : (
                      <FilterValueInput field={selectedField} value={value} onChange={setValue} />
                    )}
                  </div>
                ) : (
                  <p className="text-[12px] text-[var(--text-muted)]">{t("pickFactorHint")}</p>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between gap-3 border-[var(--border)] border-t px-5 py-4">
              <span
                aria-live="off"
                className="font-num text-[13px] text-[var(--text-muted)] tabular-nums"
              >
                {selectedField
                  ? t("livePreview", { count: previewQuery.data?.funnel.setup ?? 0 })
                  : ""}
              </span>
              <div className="flex gap-2">
                <Dialog.Close className="min-h-10 rounded-[var(--radius-pill)] border border-[var(--border)] px-4 text-[13px] text-[var(--text-muted)] hover:text-[var(--text)]">
                  {t("cancel")}
                </Dialog.Close>
                <button
                  type="button"
                  disabled={!selectedField}
                  onClick={handleConfirm}
                  className="min-h-10 rounded-[var(--radius-pill)] bg-[var(--accent)] px-4 text-[13px] text-[var(--bg)] disabled:opacity-50"
                >
                  {t("confirm")}
                </button>
              </div>
            </div>
          </motion.div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
