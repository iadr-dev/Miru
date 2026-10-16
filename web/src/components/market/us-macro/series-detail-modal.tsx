"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { SeriesDetailBody } from "@/components/market/us-macro/series-detail-body";
import { ExpandModal } from "@/components/ui/expand-modal";
import { useSeriesLabel } from "@/hooks/use-series-label";
import type { DashboardSeries } from "@/types/market/us-macro";

/** Opens one indicator's history, units, metadata and (for tracked series) revisions in the shared
 * `ExpandModal`, which owns the focus trap, Esc and focus return. `series` stays set while the
 * dialog animates out, so the content does not flash empty. */
export function SeriesDetailModal({
  series,
  open,
  onClose,
}: {
  readonly series: DashboardSeries | null;
  readonly open: boolean;
  readonly onClose: () => void;
}): ReactNode {
  const t = useTranslations("market.cards.usMacro.detail");
  const seriesLabel = useSeriesLabel();

  return (
    <ExpandModal
      open={open && series !== null}
      title={series ? seriesLabel(series.series_id, series.label) : t("title")}
      closeLabel={t("close")}
      onClose={onClose}
    >
      {series ? (
        <SeriesDetailBody key={`${series.series_id}-${series.units}`} series={series} />
      ) : null}
    </ExpandModal>
  );
}
