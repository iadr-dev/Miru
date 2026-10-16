"use client";

import { useLocale, useTranslations } from "next-intl";
import { type ReactNode, useCallback, useState } from "react";
import { CardSkeleton } from "@/components/market/card-skeleton";
import { IndicatorGroupCard } from "@/components/market/us-macro/indicator-group-card";
import { SeriesDetailModal } from "@/components/market/us-macro/series-detail-modal";
import { YieldCurvePanel } from "@/components/market/us-macro/yield-curve-panel";
import { QueryState } from "@/components/ui/query-state";
import { useUsMacroDashboard } from "@/hooks/use-us-macro";
import { formatMacroDay } from "@/lib/us-macro-format";
import type { DashboardSeries, UsMacroDashboardResponse } from "@/types/market/us-macro";

/** 美國經濟數據: the yield-curve panel, then one card per indicator group. A row opens the
 * series detail dialog. Descriptive context only — the API's currentness caveat sits under it. */
export function UsMacroSection(): ReactNode {
  const t = useTranslations("market.cards.usMacro");
  const query = useUsMacroDashboard();
  const [selected, setSelected] = useState<DashboardSeries | null>(null);
  const [open, setOpen] = useState(false);

  const openSeries = useCallback((series: DashboardSeries) => {
    setSelected(series);
    setOpen(true);
  }, []);
  const close = useCallback(() => setOpen(false), []);

  return (
    <section aria-labelledby="us-macro-title" className="flex flex-col gap-4">
      <div>
        <h2 id="us-macro-title" className="font-semibold text-[16px] text-[var(--text)]">
          {t("title")}
        </h2>
        <p className="mt-0.5 text-[12.5px] text-[var(--text-muted)]">{t("description")}</p>
      </div>
      <QueryState query={query} skeleton={<CardSkeleton height={520} />}>
        {(data) => <DashboardBody data={data} onOpenSeries={openSeries} />}
      </QueryState>
      <SeriesDetailModal series={selected} open={open} onClose={close} />
    </section>
  );
}

function DashboardBody({
  data,
  onOpenSeries,
}: {
  readonly data: UsMacroDashboardResponse;
  readonly onOpenSeries: (series: DashboardSeries) => void;
}): ReactNode {
  const t = useTranslations("market.cards.usMacro");
  const locale = useLocale();

  if (!data.data_available) {
    return (
      <div className="surface-card flex flex-col items-center gap-1 px-6 py-10 text-center">
        <p className="font-medium text-[14px] text-[var(--text)]">{t("unavailableTitle")}</p>
        <p className="text-[13px] text-[var(--text-muted)]">{t("unavailableDescription")}</p>
      </div>
    );
  }

  const groupPanels = data.panels.filter((panel) => panel.group !== "yield_curve_recession");
  return (
    <>
      <YieldCurvePanel spreads={data.yield_curve} recessionBands={data.recession_bands} />
      <div className="grid gap-4 lg:grid-cols-2">
        {groupPanels.map((panel) => (
          <IndicatorGroupCard
            key={panel.group}
            panel={panel}
            asOf={data.as_of}
            onOpenSeries={onOpenSeries}
          />
        ))}
      </div>
      <div className="flex flex-col gap-0.5 text-[11.5px] text-[var(--text-muted)]">
        {data.as_of ? (
          <p className="font-num tabular-nums">
            {t("asOf", { date: formatMacroDay(data.as_of, locale) })}
          </p>
        ) : null}
        <p>{data.currentness_caveat}</p>
      </div>
    </>
  );
}
