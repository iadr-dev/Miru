"use client";

import { CheckCircleIcon, ClockIcon } from "@phosphor-icons/react";
import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useSeriesLabel } from "@/hooks/use-series-label";
import { cn } from "@/lib/cn";
import { formatMacroChange, formatMacroValue } from "@/lib/us-macro-format";
import type { ReleaseEntry as ReleaseEntryData, ReleaseSeries } from "@/types/market/us-macro";

/** A calm, neutral status chip — upcoming or released. Never a red/green signal. */
function StatusChip({ status }: { readonly status: ReleaseEntryData["status"] }): ReactNode {
  const t = useTranslations("market.cards.usMacro.releases.status");
  const Icon = status === "released" ? CheckCircleIcon : ClockIcon;
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-[var(--radius-pill)] px-2.5 py-0.5 text-[11.5px]",
        status === "released"
          ? "bg-[var(--accent-weak)] text-[var(--accent-text)]"
          : "bg-[var(--surface-2)] text-[var(--text-muted)]",
      )}
    >
      <Icon size={12} weight="regular" aria-hidden />
      {t(status)}
    </span>
  );
}

function ReleaseSeriesRow({ series }: { readonly series: ReleaseSeries }): ReactNode {
  const t = useTranslations("market.cards.usMacro.releases");
  const locale = useLocale();
  const seriesLabel = useSeriesLabel();

  return (
    <li className="flex flex-col gap-0.5 py-2">
      <span className="text-[13px] text-[var(--text)]">
        {seriesLabel(series.series_id, series.label)}
      </span>
      <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 font-num text-[12px] text-[var(--text-muted)] tabular-nums">
        <span>
          {t("latest")}{" "}
          <span className="text-[var(--text)]">
            {formatMacroValue(series.latest?.value ?? null, series.unit_kind, locale)}
          </span>
        </span>
        <span>
          {t("previous")}{" "}
          {formatMacroValue(series.previous?.value ?? null, series.unit_kind, locale)}
        </span>
        <span>
          {t("change")} {formatMacroChange(series.change, series.unit_kind, locale)}
        </span>
      </span>
    </li>
  );
}

/** One scheduled release: its status, name and the series it covers with latest vs previous. */
export function ReleaseEntry({ release }: { readonly release: ReleaseEntryData }): ReactNode {
  const t = useTranslations("market.cards.usMacro.releases");
  return (
    <li className="rounded-[var(--radius-input)] bg-[var(--surface-2)] px-3.5 py-3">
      <div className="flex items-start justify-between gap-3">
        <h4 className="min-w-0 break-words font-medium text-[13.5px] text-[var(--text)]">
          {release.release_name}
        </h4>
        <StatusChip status={release.status} />
      </div>
      {release.series.length === 0 ? (
        <p className="mt-1.5 text-[12px] text-[var(--text-muted)]">{t("noSeries")}</p>
      ) : (
        <ul className="mt-1 divide-y divide-[var(--border)]">
          {release.series.map((series) => (
            <ReleaseSeriesRow key={`${series.series_id}-${series.units}`} series={series} />
          ))}
        </ul>
      )}
    </li>
  );
}
