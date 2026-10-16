"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { IndicatorRow } from "@/components/market/us-macro/indicator-row";
import { GROUP_CHART_COLOR } from "@/lib/us-macro-chart";
import type { DashboardPanel, DashboardSeries } from "@/types/market/us-macro";

/** One macro group (inflation, labor, …) as a card of indicator rows. Each group draws its
 * sparklines in one categorical chart colour. */
export function IndicatorGroupCard({
  panel,
  asOf,
  onOpenSeries,
}: {
  readonly panel: DashboardPanel;
  readonly asOf: string | null;
  readonly onOpenSeries: (series: DashboardSeries) => void;
}): ReactNode {
  const t = useTranslations("market.cards.usMacro");
  const color = GROUP_CHART_COLOR[panel.group];

  return (
    <section
      aria-labelledby={`us-macro-group-${panel.group}`}
      className="surface-card flex min-w-0 flex-col p-4 sm:p-5"
    >
      <h3
        id={`us-macro-group-${panel.group}`}
        className="font-semibold text-[15px] text-[var(--text)]"
      >
        {t(`groups.${panel.group}`)}
      </h3>
      <p className="mt-0.5 mb-2 text-[12px] text-[var(--text-muted)]">
        {t(`groupDescriptions.${panel.group}`)}
      </p>
      {panel.series.length === 0 ? (
        <p className="py-4 text-[13px] text-[var(--text-muted)]">{t("groupEmpty")}</p>
      ) : (
        <ul className="-mx-2 flex flex-col divide-y divide-[var(--border)]">
          {panel.series.map((series) => (
            <IndicatorRow
              key={`${series.series_id}-${series.units}`}
              series={series}
              color={color}
              asOf={asOf}
              onOpen={onOpenSeries}
            />
          ))}
        </ul>
      )}
    </section>
  );
}
