"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { Sparkline } from "@/components/market/sparkline";
import { useSeriesLabel } from "@/hooks/use-series-label";
import { cn } from "@/lib/cn";
import {
  formatMacroChange,
  formatMacroDay,
  formatMacroValue,
  isOlderThanUsual,
} from "@/lib/us-macro-format";
import type { DashboardSeries } from "@/types/market/us-macro";

/** One indicator in a group card: name, latest value, a neutral signed change vs the prior
 * observation, the observation date, a tiny history line and the next release date. The whole row
 * is one button that opens the series detail. The change is plain muted text — these are not price
 * moves, so no direction colour or arrow appears anywhere in the row. */
export function IndicatorRow({
  series,
  color,
  asOf,
  onOpen,
}: {
  readonly series: DashboardSeries;
  readonly color: string;
  readonly asOf: string | null;
  readonly onOpen: (series: DashboardSeries) => void;
}): ReactNode {
  const t = useTranslations("market.cards.usMacro.row");
  const tUnits = useTranslations("market.cards.usMacro.units");
  const locale = useLocale();
  const seriesLabel = useSeriesLabel();

  const name = seriesLabel(series.series_id, series.label);
  const unitsSuffix = series.units === "lin" ? "" : ` (${tUnits(series.units)})`;
  const older = isOlderThanUsual(series.last_observation_date, series.frequency, asOf);
  const sparkValues = series.sparkline.map((point) => point.value);

  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(series)}
        aria-haspopup="dialog"
        aria-label={t("open", { name: `${name}${unitsSuffix}` })}
        className={cn(
          "flex min-h-11 w-full flex-col gap-1 rounded-[var(--radius-input)] px-2 py-2 text-left transition-colors",
          "hover:bg-[var(--surface-2)] active:bg-[var(--surface-3)]",
          "focus-visible:outline-2 focus-visible:outline-[var(--accent)] focus-visible:outline-offset-2",
        )}
      >
        <span className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-[13px] text-[var(--text)]">
            {name}
            <span className="text-[var(--text-muted)]">{unitsSuffix}</span>
          </span>
          <span className="h-6 w-14 shrink-0">
            <Sparkline values={sparkValues} color={color} />
          </span>
          <span className="w-[5.5rem] shrink-0 text-right font-num font-semibold text-[14px] text-[var(--text)] tabular-nums">
            {formatMacroValue(series.latest?.value ?? null, series.unit_kind, locale)}
          </span>
        </span>
        <span className="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-[11.5px] text-[var(--text-muted)]">
          <span className="font-num tabular-nums">
            {t("change", {
              change: formatMacroChange(series.change, series.unit_kind, locale),
            })}
          </span>
          {series.last_observation_date ? (
            <span className="font-num tabular-nums">
              {t("observed", { date: formatMacroDay(series.last_observation_date, locale) })}
            </span>
          ) : null}
          {older ? <span>{t("older")}</span> : null}
          {series.next_release_date ? (
            <span className="font-num tabular-nums">
              {t("nextRelease", { date: formatMacroDay(series.next_release_date, locale) })}
            </span>
          ) : null}
        </span>
      </button>
    </li>
  );
}
