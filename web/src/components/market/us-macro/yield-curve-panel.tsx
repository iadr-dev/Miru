"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ChipGroup } from "@/components/market/us-macro/chip-group";
import { MacroLineChart } from "@/components/market/us-macro/macro-line-chart";
import { YieldSpreadStatus } from "@/components/market/us-macro/yield-spread-status";
import { QueryFailure } from "@/components/ui/query-state";
import { useUsMacroSeries } from "@/hooks/use-us-macro";
import {
  clampRecessionBands,
  RANGE_KEYS,
  RANGE_YEARS,
  type RangeKey,
  sliceToRange,
  toTimestamp,
  yearsBefore,
} from "@/lib/us-macro-chart";
import type { RecessionBand, YieldCurveSpread } from "@/types/market/us-macro";

const SPREAD_IDS = ["T10Y2Y", "T10Y3M"] as const;
// Categorical tokens only — the signed up/down pair is for price moves, not a yield spread.
const SPREAD_COLORS = ["var(--chart-1)", "var(--chart-3)"] as const;
const SPREAD_COLOR_BY_ID: Readonly<Record<string, string>> = {
  [SPREAD_IDS[0]]: SPREAD_COLORS[0],
  [SPREAD_IDS[1]]: SPREAD_COLORS[1],
};
const HISTORY_YEARS = 5;
// Five years of a daily series is about 1,300 observations.
const HISTORY_LIMIT = 1500;
const CHART_HEIGHT = 260;

/** 殖利率曲線與景氣衰退: the inversion state of the 10Y-2Y and 10Y-3M spreads in neutral words,
 * plus both spreads' history with officially dated recession periods shaded and a zero line. */
export function YieldCurvePanel({
  spreads,
  recessionBands,
}: {
  readonly spreads: readonly YieldCurveSpread[];
  readonly recessionBands: readonly RecessionBand[];
}): ReactNode {
  const t = useTranslations("market.cards.usMacro.yieldCurve");
  const tRange = useTranslations("market.cards.usMacro.ranges");
  const [range, setRange] = useState<RangeKey>("5Y");
  const startDate = useMemo(() => yearsBefore(new Date(), HISTORY_YEARS), []);

  const first = useUsMacroSeries({
    seriesId: SPREAD_IDS[0],
    units: "lin",
    startDate,
    limit: HISTORY_LIMIT,
  });
  const second = useUsMacroSeries({
    seriesId: SPREAD_IDS[1],
    units: "lin",
    startDate,
    limit: HISTORY_LIMIT,
  });
  const queries = [first, second] as const;
  const loading = queries.some((query) => query.isLoading);
  const failed = queries.find((query) => query.isError);

  const years = RANGE_YEARS[range];
  const lines = SPREAD_IDS.map((id, index) => ({
    id,
    label: t(`spread.${id}`),
    color: SPREAD_COLORS[index] ?? "var(--chart-1)",
    points: sliceToRange(queries[index]?.data?.points ?? [], years),
  }));
  const withData = lines.filter((line) => line.points.length > 0);
  const windowStart = Math.min(...withData.map((line) => toTimestamp(line.points[0]?.date ?? "")));
  const windowEnd = Math.max(
    ...withData.map((line) => toTimestamp(line.points.at(-1)?.date ?? "")),
  );
  const bands =
    withData.length > 0 ? clampRecessionBands(recessionBands, windowStart, windowEnd) : [];
  const unavailable = !loading && !failed && withData.length === 0;

  return (
    <section className="surface-card flex flex-col gap-4 p-4 sm:p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="font-semibold text-[15px] text-[var(--text)]">{t("title")}</h3>
          <p className="mt-0.5 text-[12.5px] text-[var(--text-muted)]">{t("description")}</p>
        </div>
        <ChipGroup
          ariaLabel={t("rangeLabel")}
          value={range}
          onChange={setRange}
          options={RANGE_KEYS.map((key) => ({ value: key, label: tRange(key) }))}
        />
      </div>

      {spreads.length > 0 ? (
        <ul className="grid gap-3 sm:grid-cols-2">
          {spreads.map((spread) => (
            <YieldSpreadStatus
              key={spread.series_id}
              spread={spread}
              color={SPREAD_COLOR_BY_ID[spread.series_id] ?? null}
            />
          ))}
        </ul>
      ) : null}

      {loading ? <CardSkeletonBody height={CHART_HEIGHT} /> : null}
      {failed ? <QueryFailure query={failed} /> : null}
      {unavailable ? (
        <p className="py-6 text-center text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : null}
      {withData.length > 0 ? (
        <>
          <MacroLineChart
            lines={withData}
            kind="percent"
            bands={bands}
            showZeroLine
            ariaLabel={t("chartLabel")}
            height={CHART_HEIGHT}
          />
          <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-[var(--text-muted)]">
            {withData.map((line) => (
              <li key={line.id} className="flex items-center gap-1.5">
                <span
                  aria-hidden
                  className="h-0.5 w-4 rounded-full"
                  style={{ backgroundColor: line.color }}
                />
                {line.label}
              </li>
            ))}
            <li className="flex items-center gap-1.5">
              <span
                aria-hidden
                className="size-3 rounded-[3px] opacity-60"
                style={{ backgroundColor: "var(--chart-6)" }}
              />
              {t("bandLegend")}
            </li>
          </ul>
          <p className="text-[12px] text-[var(--text-muted)]">{t("recessionNote")}</p>
        </>
      ) : null}
    </section>
  );
}
