"use client";

import { ArrowRightIcon, MagnifyingGlassIcon } from "@phosphor-icons/react/dist/ssr";
import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { cn } from "@/lib/cn";
import type { ScreenerPreset } from "@/types/screener";

const DIRECTION_COLOR: Record<ScreenerPreset["direction"], string> = {
  bull: "var(--up)",
  bear: "var(--down)",
  neutral: "var(--border)",
};

/** Deterministic `--chart-1..6` token per category name — same hash-to-token convention
 * `hashToChartToken` establishes elsewhere for a variable-label categorical fact, kept local here
 * since presets are the only screener surface that needs a category accent color. */
const CHART_TOKENS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
] as const;

function categoryColor(category: string): string {
  let hash = 0;
  for (let i = 0; i < category.length; i += 1) {
    hash = (hash * 31 + category.charCodeAt(i)) >>> 0;
  }
  return CHART_TOKENS[hash % CHART_TOKENS.length] ?? CHART_TOKENS[0];
}

const ALL_CATEGORY = "__all__";

/** Category filter tabs + search + a card grid (README.md §3) — each card's left edge carries
 * its category's accent color (top strip) plus a directional bull/bear tick (README.md §3's
 * documented 紅漲綠跌 exception for the preset thesis cue), a match-count badge, and a
 * "套用範本 →" call-to-action row so the card reads as an action, not just an info tile.
 * Selecting a card loads its conditions into the builder — never a black box. */
export function PresetLibrary({
  presets,
  activeKey,
  onSelect,
}: {
  readonly presets: ScreenerPreset[];
  readonly activeKey: string | null;
  readonly onSelect: (preset: ScreenerPreset) => void;
}): ReactNode {
  const t = useTranslations("screener.presets");
  const [search, setSearch] = useState("");
  const [category, setCategory] = useState<string>(ALL_CATEGORY);

  const categories = useMemo(
    () => Array.from(new Set(presets.map((preset) => preset.category))),
    [presets],
  );

  const filtered = presets.filter((preset) => {
    const matchesCategory = category === ALL_CATEGORY || preset.category === category;
    const matchesSearch =
      search.trim().length === 0 ||
      preset.label_zh.includes(search) ||
      preset.label_en.toLowerCase().includes(search.toLowerCase());
    return matchesCategory && matchesSearch;
  });

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex items-center justify-between gap-3">
        <h2 className="font-semibold text-[14px] text-[var(--text)]">{t("title")}</h2>
        <div className="relative">
          <MagnifyingGlassIcon
            size={14}
            weight="regular"
            className="pointer-events-none absolute inset-y-0 left-2.5 my-auto text-[var(--text-muted)]"
          />
          <input
            type="search"
            name="preset_search"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t("search")}
            aria-label={t("search")}
            className="min-h-11 w-48 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] pl-8 pr-3 text-[var(--text)] text-sm focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
          />
        </div>
      </div>

      <div role="tablist" className="flex flex-wrap gap-1.5">
        <button
          type="button"
          role="tab"
          aria-selected={category === ALL_CATEGORY}
          onClick={() => setCategory(ALL_CATEGORY)}
          className={cn(
            "min-h-9 rounded-[var(--radius-pill)] px-3 text-[12px] transition-colors",
            category === ALL_CATEGORY
              ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
              : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t("allCategories")} ({presets.length})
        </button>
        {categories.map((cat) => {
          const count = presets.filter((preset) => preset.category === cat).length;
          const active = category === cat;
          return (
            <button
              key={cat}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setCategory(cat)}
              className={cn(
                "min-h-9 rounded-[var(--radius-pill)] px-3 text-[12px] transition-colors",
                active
                  ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
                  : "border border-[var(--border)] text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {cat} ({count})
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {filtered.map((preset) => {
          const active = preset.key === activeKey;
          const accent = categoryColor(preset.category);
          return (
            <button
              key={preset.key}
              type="button"
              aria-pressed={active}
              onClick={() => onSelect(preset)}
              className={cn(
                "group flex flex-col gap-2 overflow-hidden rounded-[var(--radius-card)] border text-left transition-colors",
                active
                  ? "border-[var(--accent)] bg-[var(--accent-weak)]"
                  : "border-[var(--border)] bg-[var(--surface)] hover:bg-[var(--surface-2)]",
              )}
            >
              <span className="h-1 w-full shrink-0" style={{ backgroundColor: accent }} />
              <div className="flex flex-col gap-2 px-3.5 pb-3.5">
                <div className="flex items-start justify-between gap-2">
                  <span className="font-medium text-[13px] text-[var(--text)]">
                    {preset.label_zh}
                  </span>
                  {preset.direction !== "neutral" ? (
                    <span
                      className="mt-0.5 size-2 shrink-0 rounded-full"
                      style={{ backgroundColor: DIRECTION_COLOR[preset.direction] }}
                      aria-hidden="true"
                    />
                  ) : null}
                </div>
                <span className="text-[12px] text-[var(--text-muted)]">{preset.description}</span>
                <span className="font-num text-[11px] text-[var(--text-muted)] tabular-nums">
                  {t("conditionCount", { count: preset.conditions.length })}
                </span>
                <div className="flex items-center justify-between pt-1">
                  <span
                    className="rounded-[var(--radius-pill)] px-2 py-0.5 font-medium text-[11px]"
                    style={{
                      backgroundColor: `color-mix(in srgb, ${accent} 16%, transparent)`,
                      color: accent,
                    }}
                  >
                    {preset.category}
                  </span>
                  <span
                    className={cn(
                      "inline-flex items-center gap-1 font-medium text-[12px] transition-colors",
                      active
                        ? "text-[var(--accent)]"
                        : "text-[var(--text-muted)] group-hover:text-[var(--accent)]",
                    )}
                  >
                    {t("applyPreset")}
                    <ArrowRightIcon size={12} weight="bold" />
                  </span>
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
