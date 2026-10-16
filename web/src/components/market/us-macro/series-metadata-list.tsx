"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { formatMacroDay } from "@/lib/us-macro-format";
import type { SeriesMetadata } from "@/types/market/us-macro";

function MetaItem({
  label,
  children,
}: {
  readonly label: string;
  readonly children: ReactNode;
}): ReactNode {
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <dt className="text-[11.5px] text-[var(--text-muted)]">{label}</dt>
      <dd className="break-words text-[13px] text-[var(--text)]">{children}</dd>
    </div>
  );
}

/** The series' descriptive facts: full name, frequency, seasonal adjustment, last update and next
 * release. Long notes stay collapsed until asked for. */
export function SeriesMetadataList({ metadata }: { readonly metadata: SeriesMetadata }): ReactNode {
  const t = useTranslations("market.cards.usMacro.detail");
  const tFrequency = useTranslations("market.cards.usMacro.frequencies");
  const locale = useLocale();
  const frequencyKey = metadata.frequency.toLowerCase();
  const frequency = tFrequency.has(frequencyKey) ? tFrequency(frequencyKey) : metadata.frequency;

  return (
    <div className="flex flex-col gap-3">
      <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
        {metadata.title ? <MetaItem label={t("fullName")}>{metadata.title}</MetaItem> : null}
        <MetaItem label={t("frequency")}>{frequency}</MetaItem>
        {metadata.seasonal_adjustment ? (
          <MetaItem label={t("seasonalAdjustment")}>{metadata.seasonal_adjustment}</MetaItem>
        ) : null}
        {metadata.units_description ? (
          <MetaItem label={t("unitsDescription")}>{metadata.units_description}</MetaItem>
        ) : null}
        {metadata.last_updated ? (
          <MetaItem label={t("lastUpdated")}>
            <span className="font-num tabular-nums">
              {formatMacroDay(metadata.last_updated, locale)}
            </span>
          </MetaItem>
        ) : null}
        {metadata.release_name ? (
          <MetaItem label={t("releaseName")}>{metadata.release_name}</MetaItem>
        ) : null}
        {metadata.next_release_date ? (
          <MetaItem label={t("nextRelease")}>
            <span className="font-num tabular-nums">
              {formatMacroDay(metadata.next_release_date, locale)}
            </span>
          </MetaItem>
        ) : null}
      </dl>
      {metadata.notes ? (
        <details className="rounded-[var(--radius-input)] bg-[var(--surface-2)] px-3.5 py-2.5">
          <summary className="min-h-6 cursor-pointer text-[13px] text-[var(--text)]">
            {t("notes")}
          </summary>
          <p className="mt-2 whitespace-pre-line break-words text-[12.5px] text-[var(--text-muted)]">
            {metadata.notes}
          </p>
        </details>
      ) : null}
    </div>
  );
}
