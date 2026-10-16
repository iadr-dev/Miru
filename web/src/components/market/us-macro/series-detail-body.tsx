"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useMemo, useState } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { ChipGroup } from "@/components/market/us-macro/chip-group";
import { MacroLineChart } from "@/components/market/us-macro/macro-line-chart";
import { RevisionsTable } from "@/components/market/us-macro/revisions-table";
import { SeriesMetadataList } from "@/components/market/us-macro/series-metadata-list";
import { QueryFailure } from "@/components/ui/query-state";
import { useSeriesLabel } from "@/hooks/use-series-label";
import { useUsMacroSeries } from "@/hooks/use-us-macro";
import { cn } from "@/lib/cn";
import {
  GROUP_CHART_COLOR,
  RANGE_KEYS,
  RANGE_YEARS,
  type RangeKey,
  yearsBefore,
} from "@/lib/us-macro-chart";
import { type DashboardSeries, isRevisionSeriesId, type MacroUnits } from "@/types/market/us-macro";

const DETAIL_LIMIT = 3000;
const CHART_HEIGHT = 260;

/** The body of the series detail dialog: units selector, range chips, the history chart, the
 * series' descriptive facts and, for the revision-tracked series, first release vs latest. Mounted
 * only while the dialog is open, so nothing is fetched for a closed dialog. */
export function SeriesDetailBody({ series }: { readonly series: DashboardSeries }): ReactNode {
  const t = useTranslations("market.cards.usMacro");
  const seriesLabel = useSeriesLabel();
  const [units, setUnits] = useState<MacroUnits>(series.units);
  const [range, setRange] = useState<RangeKey>("5Y");
  const startDate = useMemo(() => yearsBefore(new Date(), RANGE_YEARS[range]), [range]);
  const query = useUsMacroSeries({
    seriesId: series.series_id,
    units,
    startDate,
    limit: DETAIL_LIMIT,
  });

  const data = query.data;
  const metadata = data?.metadata ?? null;
  const availableUnits = metadata?.available_units ?? [series.units];
  const points = data?.points ?? [];
  const name = seriesLabel(series.series_id, series.label);
  const kind = metadata?.unit_kind ?? series.unit_kind;
  const color = GROUP_CHART_COLOR[metadata?.group ?? "inflation"];
  const crossesZero =
    points.length > 0 &&
    Math.min(...points.map((point) => point.value)) < 0 &&
    Math.max(...points.map((point) => point.value)) > 0;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        {availableUnits.length > 1 ? (
          <ChipGroup
            ariaLabel={t("detail.unitsLabel")}
            value={units}
            onChange={setUnits}
            options={availableUnits.map((value) => ({ value, label: t(`units.${value}`) }))}
          />
        ) : (
          <span />
        )}
        <ChipGroup
          ariaLabel={t("detail.rangeLabel")}
          value={range}
          onChange={setRange}
          options={RANGE_KEYS.map((key) => ({ value: key, label: t(`ranges.${key}`) }))}
        />
      </div>

      {query.isLoading ? <CardSkeletonBody height={CHART_HEIGHT} /> : null}
      {query.isError ? <QueryFailure query={query} /> : null}
      {data && !data.data_available ? (
        <p className="py-6 text-center text-[13px] text-[var(--text-muted)]">
          {t("detail.unavailable")}
        </p>
      ) : null}
      {data?.data_available && points.length === 0 ? (
        <p className="py-6 text-center text-[13px] text-[var(--text-muted)]">
          {t("detail.noPoints")}
        </p>
      ) : null}
      {points.length > 0 ? (
        <div className={cn("transition-opacity", query.isPlaceholderData && "opacity-60")}>
          <MacroLineChart
            lines={[{ id: series.series_id, label: name, color, points }]}
            kind={kind}
            bands={[]}
            showZeroLine={crossesZero}
            ariaLabel={t("detail.chartLabel", { name })}
            height={CHART_HEIGHT}
          />
        </div>
      ) : null}

      {metadata ? <SeriesMetadataList metadata={metadata} /> : null}
      {isRevisionSeriesId(series.series_id) ? <RevisionsTable seriesId={series.series_id} /> : null}
      {data ? (
        <p className="text-[11.5px] text-[var(--text-muted)]">{data.currentness_caveat}</p>
      ) : null}
    </div>
  );
}
