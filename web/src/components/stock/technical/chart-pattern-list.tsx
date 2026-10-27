"use client";

import type { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  type PatternLevels,
  type PatternLineStyle,
  patternKey,
  patternLevels,
  patternStroke,
  sortPatternsNewestFirst,
} from "@/lib/chart-patterns";
import { cn } from "@/lib/cn";
import type { ChartPatternHit, ChartPatternStatus } from "@/types/stock/technical";

type PatternsT = ReturnType<typeof useTranslations>;

const STATUS_CHIP: Record<ChartPatternStatus, string> = {
  confirmed: "bg-[var(--accent-weak)] text-[var(--accent-text)]",
  forming: "border border-[var(--border)] bg-[var(--surface-2)] text-[var(--text)]",
  failed: "border border-[var(--border)] bg-[var(--surface-2)] text-[var(--text-muted)]",
};

// Mirrors the chart's line styles (solid confirmed, dashed forming, dotted failed) on the swatch.
const BORDER_STYLE: Record<PatternLineStyle, "solid" | "dotted" | "dashed"> = {
  0: "solid",
  1: "dotted",
  2: "dashed",
};

const PRICE_FORMAT = new Intl.NumberFormat("en-US", {
  maximumFractionDigits: 2,
  useGrouping: false,
});

function levelParts(levels: PatternLevels, t: PatternsT): string[] {
  const parts: string[] = [];
  const add = (key: "neckline" | "upper" | "lower", value: number | null): void => {
    if (value !== null)
      parts.push(t(`chartPatterns.level.${key}`, { value: PRICE_FORMAT.format(value) }));
  };
  add("neckline", levels.neckline);
  add("upper", levels.upper);
  add("lower", levels.lower);
  parts.push(t("chartPatterns.level.height", { value: PRICE_FORMAT.format(levels.height) }));
  return parts;
}

function resolutionLine(hit: ChartPatternHit, t: PatternsT): string | null {
  if (hit.failed_at) return t("chartPatterns.failedOn", { date: hit.failed_at.trade_date });
  if (hit.confirmed_at) {
    const confirmed = t("chartPatterns.confirmedOn", { date: hit.confirmed_at.trade_date });
    if (!hit.broken_side) return confirmed;
    return [confirmed, t(`chartPatterns.side.${hit.broken_side}`)].join(" · ");
  }
  return null;
}

function PatternRow({
  hit,
  selected,
  onSelect,
  t,
}: {
  readonly hit: ChartPatternHit;
  readonly selected: boolean;
  readonly onSelect: (key: string | null) => void;
  readonly t: PatternsT;
}): ReactNode {
  const key = patternKey(hit);
  const stroke = patternStroke(hit);
  const name = t(`chartPatterns.pattern.${hit.pattern}`);
  const resolution = resolutionLine(hit, t);
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(selected ? null : key)}
        aria-pressed={selected}
        aria-label={t("chartPatterns.select", { name })}
        className={cn(
          "flex min-h-[44px] w-full min-w-0 flex-col gap-1 rounded-[var(--radius-input)] border px-3 py-2 text-left transition-colors",
          "sm:grid sm:grid-cols-[minmax(0,13rem)_minmax(0,1fr)_minmax(0,14rem)] sm:items-center sm:gap-x-4",
          selected
            ? "border-[var(--accent)] bg-[var(--accent-weak)]"
            : "border-transparent hover:bg-[var(--surface-2)]",
        )}
      >
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <span
            aria-hidden
            className="inline-block w-5 shrink-0 border-t-2"
            style={{
              borderTopColor: stroke.token,
              borderTopStyle: BORDER_STYLE[stroke.lineStyle],
            }}
          />
          <span className="font-medium text-[13px] text-[var(--text)]">{name}</span>
          <span
            className={cn(
              "rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px]",
              STATUS_CHIP[hit.status],
            )}
          >
            {t(`chartPatterns.status.${hit.status}`)}
          </span>
          <span className="text-[11px] text-[var(--text-muted)]">
            {t(`chartPatterns.family.${hit.family}`)}
          </span>
        </span>
        <span className="min-w-0 break-words font-num text-[12px] text-[var(--text-muted)] tabular-nums">
          {levelParts(patternLevels(hit), t).join(" · ")}
        </span>
        <span className="flex min-w-0 flex-col gap-0.5 text-[12px] text-[var(--text-muted)] sm:items-end">
          <span className="font-num tabular-nums">
            {t("chartPatterns.range", {
              start: hit.window_start.trade_date,
              end: hit.window_end.trade_date,
            })}
          </span>
          {resolution ? <span className="font-num tabular-nums">{resolution}</span> : null}
        </span>
      </button>
    </li>
  );
}

/** The list under the candlestick chart: the accessible equivalent of the pattern lines (a screen
 * reader never sees the canvas) and where a pattern is picked to be highlighted on the chart.
 * Newest structure first; every hit is listed, drawn or not. */
export function ChartPatternList({
  hits,
  selectedKey,
  onSelect,
  t,
}: {
  readonly hits: readonly ChartPatternHit[];
  readonly selectedKey: string | null;
  readonly onSelect: (key: string | null) => void;
  readonly t: PatternsT;
}): ReactNode {
  const ordered = sortPatternsNewestFirst(hits);
  return (
    <div className="mt-4">
      <h3 className="mb-1 font-semibold text-[13px] text-[var(--text)]">
        {t("chartPatterns.title")}
      </h3>
      <p className="mb-2 text-[11px] text-[var(--text-muted)]">{t("chartPatterns.statusNote")}</p>
      {ordered.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("chartPatterns.empty")}</p>
      ) : (
        <ul aria-label={t("chartPatterns.listLabel")} className="flex flex-col gap-1">
          {ordered.map((hit) => (
            <PatternRow
              key={patternKey(hit)}
              hit={hit}
              selected={patternKey(hit) === selectedKey}
              onSelect={onSelect}
              t={t}
            />
          ))}
        </ul>
      )}
    </div>
  );
}
