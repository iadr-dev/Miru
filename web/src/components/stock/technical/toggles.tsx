"use client";

import type { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import {
  MA_COLOR,
  MA_WINDOWS,
  type MaWindow,
  type RangeKey,
  type SubOverlay,
  type Timeframe,
} from "@/components/stock/technical/shared";
import { cn } from "@/lib/cn";
import type { Market } from "@/types/settings";

const RANGE_KEYS: readonly RangeKey[] = ["1m", "3m", "6m", "ytd", "1y", "5y"];

export function TimeframeToggle({
  value,
  onChange,
  t,
}: {
  readonly value: Timeframe;
  readonly onChange: (v: Timeframe) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const options: readonly Timeframe[] = ["day", "week", "month"];
  return (
    <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
      {options.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          aria-pressed={value === opt}
          className={cn(
            "rounded-[var(--radius-pill)] px-2.5 py-1 font-medium text-[12px] transition-colors",
            value === opt
              ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t(`timeframe.${opt}`)}
        </button>
      ))}
    </div>
  );
}

export function RangeToggle({
  value,
  onChange,
  t,
}: {
  readonly value: RangeKey;
  readonly onChange: (v: RangeKey) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="flex gap-1 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-0.5">
      {RANGE_KEYS.map((opt) => (
        <button
          key={opt}
          type="button"
          onClick={() => onChange(opt)}
          aria-pressed={value === opt}
          className={cn(
            "rounded-[var(--radius-pill)] px-2 py-1 font-medium text-[11px] transition-colors",
            value === opt
              ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
              : "text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t(`range.${opt}`)}
        </button>
      ))}
    </div>
  );
}

export function MaToggleRow({
  visible,
  onToggle,
  t,
}: {
  readonly visible: ReadonlySet<MaWindow>;
  readonly onToggle: (next: ReadonlySet<MaWindow>) => void;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-2">
      <span className="text-[11px] text-[var(--text-muted)]">{t("maLabel")}</span>
      {MA_WINDOWS.map((window) => {
        const active = visible.has(window);
        return (
          <button
            key={window}
            type="button"
            onClick={() => {
              const next = new Set(visible);
              if (active) next.delete(window);
              else next.add(window);
              onToggle(next);
            }}
            aria-pressed={active}
            className="flex items-center gap-1 rounded-[var(--radius-pill)] px-2 py-1 text-[11px] transition-opacity"
            style={{
              backgroundColor: active
                ? `color-mix(in srgb, ${MA_COLOR[window]} 16%, transparent)`
                : "var(--surface-2)",
              color: active ? MA_COLOR[window] : "var(--text-muted)",
              opacity: active ? 1 : 0.6,
            }}
          >
            MA{window}
          </button>
        );
      })}
    </div>
  );
}

export function StructureToggleRow({
  showSupportResistance,
  onToggleSupportResistance,
  showPatterns,
  onTogglePatterns,
  showEvents,
  onToggleEvents,
  t,
}: {
  readonly showSupportResistance: boolean;
  readonly onToggleSupportResistance: (v: boolean) => void;
  readonly showPatterns: boolean;
  readonly onTogglePatterns: (v: boolean) => void;
  /** `undefined` hides the chip (markets with no event feed). */
  readonly showEvents?: boolean | undefined;
  readonly onToggleEvents?: ((v: boolean) => void) | undefined;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <span className="text-[11px] text-[var(--text-muted)]">{t("structureLabel")}</span>
      <button
        type="button"
        onClick={() => onToggleSupportResistance(!showSupportResistance)}
        aria-pressed={showSupportResistance}
        className={cn(
          "rounded-[var(--radius-pill)] px-2.5 py-1 text-[11px] transition-colors",
          showSupportResistance
            ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
            : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
        )}
      >
        {t("structureSupportResistance")}
      </button>
      <button
        type="button"
        onClick={() => onTogglePatterns(!showPatterns)}
        aria-pressed={showPatterns}
        className={cn(
          "rounded-[var(--radius-pill)] px-2.5 py-1 text-[11px] transition-colors",
          showPatterns
            ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
            : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
        )}
      >
        {t("structurePatterns")}
      </button>
      {showEvents !== undefined && onToggleEvents ? (
        <button
          type="button"
          onClick={() => onToggleEvents(!showEvents)}
          aria-pressed={showEvents}
          className={cn(
            "inline-flex min-h-[44px] items-center rounded-[var(--radius-pill)] px-3 text-[12px] transition-colors",
            showEvents
              ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
              : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
          )}
        >
          {t("events.toggle")}
        </button>
      ) : null}
    </div>
  );
}

const SUB_OVERLAYS: readonly SubOverlay[] = [
  "bollinger",
  "kd",
  "macd",
  "institutional",
  "bigHolders",
  "foreignHolding",
  "marginShort",
  "securitiesLending",
  "retail",
];

// TWSE/TDCC chip-flow/holder concepts — backed by FinMind-only capabilities with no non-TW
// equivalent (institutional flow, shareholder dispersion, foreign holding, margin/short,
// securities lending). `bollinger`/`kd`/`macd` are universal technical indicators, never gated.
const TW_ONLY_OVERLAYS: ReadonlySet<SubOverlay> = new Set([
  "institutional",
  "bigHolders",
  "foreignHolding",
  "marginShort",
  "securitiesLending",
  "retail",
]);

export function OverlayToggleRow({
  value,
  onChange,
  market,
  t,
}: {
  readonly value: ReadonlySet<SubOverlay>;
  readonly onChange: (v: ReadonlySet<SubOverlay>) => void;
  readonly market: Market;
  readonly t: ReturnType<typeof useTranslations>;
}): ReactNode {
  const overlays = SUB_OVERLAYS.filter(
    (overlay) => market === "TW" || !TW_ONLY_OVERLAYS.has(overlay),
  );
  return (
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <span className="text-[11px] text-[var(--text-muted)]">{t("overlayLabel")}</span>
      {overlays.map((overlay) => {
        const active = value.has(overlay);
        return (
          <button
            key={overlay}
            type="button"
            onClick={() => {
              const next = new Set(value);
              if (active) next.delete(overlay);
              else next.add(overlay);
              onChange(next);
            }}
            aria-pressed={active}
            className={cn(
              "rounded-[var(--radius-pill)] px-2.5 py-1 text-[11px] transition-colors",
              active
                ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
                : "bg-[var(--surface-2)] text-[var(--text-muted)] hover:text-[var(--text)]",
            )}
          >
            {t(`overlay.${overlay}`)}
          </button>
        );
      })}
    </div>
  );
}
