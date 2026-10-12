"use client";

import { BookmarkSimpleIcon, CaretRightIcon, TrashIcon } from "@phosphor-icons/react/dist/ssr";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import {
  useDeleteScreenerScreen,
  useRunScreener,
  useSaveScreenerScreen,
  useScreenerScreens,
} from "@/hooks/use-screener";
import { hashToChartToken } from "@/lib/chart-token-hash";
import type {
  FactorCatalog,
  ScreenerGateOverrides,
  ScreenerMarket,
  ScreenerRunCondition,
  ScreenerScreen,
} from "@/types/screener";
import { isFunnelUniverseKnown } from "@/types/screener";

/** `factor 大於等於 value` — the same operator vocabulary `ConditionRow` renders, joined into one
 * line per screen. Falls back to the raw factor key if the catalog hasn't loaded yet (still
 * legible, just less pretty) rather than blocking the summary render on `catalogQuery`. */
function summarizeConditions(
  conditions: ScreenerRunCondition[],
  catalog: FactorCatalog | undefined,
  operatorLabel: (op: ScreenerRunCondition["op"]) => string,
): string {
  if (conditions.length === 0) return "";
  const fieldsByKey = new Map((catalog?.fields ?? []).map((field) => [field.key, field] as const));
  const signalsByKey = new Map(
    (catalog?.signals ?? []).map((signal) => [signal.key, signal] as const),
  );
  return conditions
    .map((condition) => {
      const signal = signalsByKey.get(condition.factor);
      if (signal) return signal.label_zh;
      const field = fieldsByKey.get(condition.factor);
      const label = field?.label_zh ?? condition.factor;
      if (condition.value === undefined || condition.value === null) return label;
      const unit = field?.unit ?? "";
      const value2 =
        condition.value2 !== undefined && condition.value2 !== null
          ? ` ~ ${condition.value2}${unit}`
          : "";
      return `${label} ${operatorLabel(condition.op)} ${condition.value}${unit}${value2}`;
    })
    .join(" + ");
}

/** One saved screen's live survivor count — a `count_only` probe against ITS OWN stored
 * conditions/gates, independent of whatever the builder currently has loaded (README.md §6's
 * "click to load instantly" implies showing what you'd get before you click). */
function SavedScreenMatchCount({
  market,
  conditions,
  match,
  gateOverrides,
  accent,
}: {
  readonly market: ScreenerMarket;
  readonly conditions: ScreenerRunCondition[];
  readonly match: "AND" | "OR";
  readonly gateOverrides: ScreenerGateOverrides | undefined;
  readonly accent: string;
}): ReactNode {
  const t = useTranslations("screener.savedScreens");
  const runQuery = useRunScreener({
    market,
    conditions,
    match,
    gateOverrides,
    countOnly: true,
  });

  if (!runQuery.data || !isFunnelUniverseKnown(runQuery.data.funnel)) return null;

  return (
    <span
      aria-live="off"
      className="shrink-0 rounded-[var(--radius-pill)] px-2 py-0.5 font-num text-[11px] tabular-nums"
      style={{ backgroundColor: `color-mix(in srgb, ${accent} 16%, transparent)`, color: accent }}
    >
      {t("matchCount", { count: runQuery.data.funnel.setup.toLocaleString() })}
    </span>
  );
}

/** List/load/save a user's saved condition sets, scoped per market (README.md §6). Each saved
 * screen renders as a card: name, a human-readable condition summary, and its own live match-count
 * probe — not just a bare name — so "load instantly" is a decision you can make before clicking.
 * Rename/CAS-dirty-indicator are a follow-up — this pass covers list/load/save/delete, the
 * operations the hooks (`use-screener.ts`) and backend CRUD already support end-to-end. */
export function SavedScreens({
  market,
  catalog,
  currentConditions,
  currentMatch,
  currentGateOverrides,
  onLoad,
}: {
  readonly market: ScreenerMarket;
  readonly catalog: FactorCatalog | undefined;
  readonly currentConditions: ScreenerRunCondition[];
  readonly currentMatch: "AND" | "OR";
  readonly currentGateOverrides: ScreenerGateOverrides;
  readonly onLoad: (screen: ScreenerScreen) => void;
}): ReactNode {
  const t = useTranslations("screener.savedScreens");
  const tOperators = useTranslations("screener.operators");
  const screensQuery = useScreenerScreens(market);
  const saveMutation = useSaveScreenerScreen(market);
  const deleteMutation = useDeleteScreenerScreen(market);
  const [name, setName] = useState("");
  const screens = screensQuery.data ?? [];

  function handleSave(): void {
    if (name.trim().length === 0) return;
    saveMutation.mutate(
      {
        name: name.trim(),
        market,
        conditions: currentConditions,
        match: currentMatch,
        gateOverrides: currentGateOverrides,
      },
      { onSuccess: () => setName("") },
    );
  }

  return (
    <div className="surface-card flex flex-col gap-2 p-3">
      <div className="flex items-baseline justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 font-medium text-[12px] text-[var(--text-muted)]">
          <BookmarkSimpleIcon size={13} weight="fill" className="text-[var(--accent)]" />
          {t("title")} ({screens.length})
        </span>
        {screens.length > 0 ? (
          <span className="text-[11px] text-[var(--text-muted)]">{t("loadHint")}</span>
        ) : null}
      </div>
      {screens.length > 0 ? (
        <div className="flex flex-col gap-1">
          {screens.map((screen) => {
            const conditions = screen.conditions as ScreenerRunCondition[];
            const summary = summarizeConditions(conditions, catalog, (op) => tOperators(op));
            const accent = hashToChartToken(screen.name);
            return (
              <div
                key={screen.id}
                className="flex items-center gap-1 rounded-[var(--radius-input)] transition-colors hover:bg-[var(--surface-2)]"
                style={{ borderLeft: `2px solid ${accent}` }}
              >
                <button
                  type="button"
                  onClick={() => onLoad(screen)}
                  className="flex min-h-9 min-w-0 flex-1 items-center gap-2 rounded-[var(--radius-input)] px-2 py-1.5 text-left"
                >
                  <span
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: accent }}
                    aria-hidden="true"
                  />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="truncate font-medium text-[13px] text-[var(--text)]">
                      {screen.name}
                    </span>
                    {summary ? (
                      <span className="truncate text-[11px] text-[var(--text-muted)]">
                        {summary}
                      </span>
                    ) : null}
                  </span>
                  <SavedScreenMatchCount
                    market={market}
                    conditions={conditions}
                    match={screen.match}
                    gateOverrides={screen.gate_overrides as ScreenerGateOverrides | undefined}
                    accent={accent}
                  />
                  <CaretRightIcon
                    size={14}
                    weight="regular"
                    className="shrink-0 text-[var(--text-muted)]"
                  />
                </button>
                <button
                  type="button"
                  aria-label={t("delete")}
                  onClick={() => deleteMutation.mutate(screen.id)}
                  className="grid size-9 shrink-0 place-items-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:text-[var(--down)]"
                >
                  <TrashIcon size={14} weight="regular" />
                </button>
              </div>
            );
          })}
        </div>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <input
          type="text"
          name="screen_name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder={t("namePlaceholder")}
          aria-label={t("namePlaceholder")}
          className="min-h-9 w-full rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface)] px-2 text-[var(--text)] text-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
        />
        <button
          type="button"
          onClick={handleSave}
          disabled={saveMutation.isPending}
          className="min-h-9 w-full whitespace-nowrap rounded-[var(--radius-pill)] bg-[var(--accent)] px-3 text-[13px] text-[var(--bg)] disabled:opacity-50"
        >
          {t("save")}
        </button>
      </div>
    </div>
  );
}
