"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { ConditionDrawer } from "@/components/screener/condition-drawer";
import { ConditionRow } from "@/components/screener/condition-row";
import { cn } from "@/lib/cn";
import type { FactorCatalog, ScreenerMarket, ScreenerRunCondition } from "@/types/screener";

type BuilderSubMode = "signals" | "conditions";

/** The pro-path filter builder (README.md §4): 訊號/條件 sub-modes, AND/OR toggle (shown once
 * ≥2 conditions), `+ 新增條件` opens the full `ConditionDrawer` slide-over rather than an inline
 * popover — the richer per-indicator config (operator + labeled slider + live match preview)
 * doesn't comfortably fit the 264px rail. `selectedTags`/`conditions`/`match` are controlled
 * state the parent (`ScreenerWorkbench`) owns, since they feed directly into `useRunScreener`'s
 * query. */
export function ConditionBuilder({
  catalog,
  market,
  conditions,
  onConditionsChange,
  selectedTags,
  onSelectedTagsChange,
  match,
  onMatchChange,
  liveCount,
}: {
  readonly catalog: FactorCatalog | undefined;
  readonly market: ScreenerMarket;
  readonly conditions: ScreenerRunCondition[];
  readonly onConditionsChange: (next: ScreenerRunCondition[]) => void;
  readonly selectedTags: string[];
  readonly onSelectedTagsChange: (next: string[]) => void;
  readonly match: "AND" | "OR";
  readonly onMatchChange: (next: "AND" | "OR") => void;
  readonly liveCount: number | undefined;
}): ReactNode {
  const t = useTranslations("screener.builder");
  const [subMode, setSubMode] = useState<BuilderSubMode>(
    catalog && catalog.signals.length > 0 ? "signals" : "conditions",
  );
  const [drawerOpen, setDrawerOpen] = useState(false);

  const fieldsByKey = new Map((catalog?.fields ?? []).map((field) => [field.key, field]));
  const totalCount = selectedTags.length + conditions.length;

  function toggleTag(tag: string): void {
    onSelectedTagsChange(
      selectedTags.includes(tag) ? selectedTags.filter((t2) => t2 !== tag) : [...selectedTags, tag],
    );
  }

  function updateCondition(index: number, patch: Partial<ScreenerRunCondition>): void {
    onConditionsChange(conditions.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }

  function removeCondition(index: number): void {
    onConditionsChange(conditions.filter((_, i) => i !== index));
  }

  return (
    <div className="flex flex-col gap-3 border-[var(--border)] border-r bg-[var(--surface)] p-3">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-[14px] text-[var(--text)]">{t("title")}</h2>
        <span
          aria-live="off"
          className="font-num text-[12px] text-[var(--text-muted)] tabular-nums"
        >
          {t("liveCount", { count: liveCount ?? totalCount })}
        </span>
      </div>

      {catalog && catalog.signals.length > 0 ? (
        <div
          role="tablist"
          className="flex gap-1 rounded-[var(--radius-input)] bg-[var(--surface-2)] p-1"
        >
          {(["signals", "conditions"] as const).map((key) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={subMode === key}
              onClick={() => setSubMode(key)}
              className={cn(
                "min-h-9 flex-1 rounded-[var(--radius-input)] px-2 text-[13px] transition-colors",
                subMode === key
                  ? "bg-[var(--surface)] font-medium text-[var(--text)]"
                  : "text-[var(--text-muted)]",
              )}
            >
              {t(key)}
            </button>
          ))}
        </div>
      ) : null}

      {/* Rendered unconditionally (visibility toggled, not mounted/unmounted) so crossing the
       * ≥2-condition threshold — which happens from inside a checkbox's own `onChange` — never
       * shifts every checkbox below it in the same layout pass. A conditionally-mounted row here
       * previously moved the element a click was targeting out from under the pointer mid-click. */}
      <div
        role="tablist"
        aria-hidden={totalCount < 2}
        className={cn(
          "flex gap-1 self-start rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-1",
          totalCount < 2 && "invisible",
        )}
      >
        {(["AND", "OR"] as const).map((key) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={match === key}
            onClick={() => onMatchChange(key)}
            className={cn(
              "min-h-9 rounded-[var(--radius-pill)] px-3 text-[12px] transition-colors",
              match === key
                ? "bg-[var(--accent-weak)] text-[var(--accent)]"
                : "text-[var(--text-muted)]",
            )}
          >
            {t(key === "AND" ? "matchAnd" : "matchOr")}
          </button>
        ))}
      </div>

      {subMode === "signals" && catalog ? (
        <div className="flex flex-col gap-1">
          {catalog.signals.map((signal) => (
            <label
              key={signal.key}
              className="flex min-h-11 items-center gap-2 rounded-[var(--radius-input)] px-2 text-[13px] text-[var(--text)] hover:bg-[var(--surface-2)]"
            >
              <input
                type="checkbox"
                checked={selectedTags.includes(signal.key)}
                onChange={() => toggleTag(signal.key)}
                className="accent-[var(--accent)]"
              />
              {signal.label_zh}
            </label>
          ))}
        </div>
      ) : null}

      {subMode === "conditions" ? (
        <div className="flex flex-col gap-2">
          {conditions.map((condition, index) => (
            <ConditionRow
              key={condition.factor}
              field={fieldsByKey.get(condition.factor)}
              condition={condition}
              onOperatorChange={(op) => updateCondition(index, { op })}
              onValueChange={(value) => updateCondition(index, { value })}
              onRemove={() => removeCondition(index)}
            />
          ))}

          <button
            type="button"
            onClick={() => setDrawerOpen(true)}
            className="min-h-11 w-full rounded-[var(--radius-input)] px-2 text-left text-[13px] text-[var(--accent)] hover:bg-[var(--surface-2)]"
          >
            + {t("addCondition")}
          </button>

          <ConditionDrawer
            open={drawerOpen}
            onOpenChange={setDrawerOpen}
            catalog={catalog}
            market={market}
            existingConditions={conditions}
            match={match}
            onConfirm={(condition) => {
              if (conditions.some((c) => c.factor === condition.factor)) return;
              onConditionsChange([...conditions, condition]);
            }}
          />
        </div>
      ) : null}
    </div>
  );
}
