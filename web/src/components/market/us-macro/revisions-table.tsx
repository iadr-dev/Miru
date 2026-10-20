"use client";

import { useLocale, useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { QueryFailure } from "@/components/ui/query-state";
import { useUsSeriesRevisions } from "@/hooks/use-us-macro";
import { formatMacroChange, formatMacroMonth, formatMacroValue } from "@/lib/us-macro-format";
import type { RevisionSeriesId } from "@/types/market/us-macro";

const NUM_CELL = "px-3 py-2 text-right font-num tabular-nums whitespace-nowrap";

/** First release vs latest value of recent observations for a revision-tracked series. The
 * revision is plain signed text (agencies restate figures; it is not a market move). */
export function RevisionsTable({ seriesId }: { readonly seriesId: RevisionSeriesId }): ReactNode {
  const t = useTranslations("market.cards.usMacro.detail.revisions");
  const locale = useLocale();
  const query = useUsSeriesRevisions(seriesId);

  return (
    <section aria-labelledby={`revisions-${seriesId}`} className="flex flex-col gap-2">
      <h3 id={`revisions-${seriesId}`} className="font-semibold text-[14px] text-[var(--text)]">
        {t("title")}
      </h3>
      {query.isLoading ? <CardSkeletonBody height={140} /> : null}
      {query.isError ? <QueryFailure query={query} /> : null}
      {query.data?.rows.length === 0 ? (
        <p className="text-[13px] text-[var(--text-muted)]">{t("empty")}</p>
      ) : null}
      {query.data && query.data.rows.length > 0 ? (
        <>
          {query.data.units_description ? (
            <p className="text-[12px] text-[var(--text-muted)]">{query.data.units_description}</p>
          ) : null}
          <div className="max-h-64 overflow-auto rounded-[var(--radius-input)] border border-[var(--border)]">
            <table className="w-full min-w-[460px] border-collapse text-[13px]">
              <thead className="sticky top-0 bg-[var(--surface-2)] text-[11.5px] text-[var(--text-muted)]">
                <tr>
                  <th scope="col" className="px-3 py-2 text-left font-normal">
                    {t("observation")}
                  </th>
                  <th scope="col" className={`${NUM_CELL} font-normal`}>
                    {t("firstRelease")}
                  </th>
                  <th scope="col" className={`${NUM_CELL} font-normal`}>
                    {t("latest")}
                  </th>
                  <th scope="col" className={`${NUM_CELL} font-normal`}>
                    {t("revision")}
                  </th>
                  <th scope="col" className={`${NUM_CELL} font-normal`}>
                    {t("vintages")}
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--border)] text-[var(--text)]">
                {[...query.data.rows]
                  .sort((a, b) => b.observation_date.localeCompare(a.observation_date))
                  .map((row) => (
                    <tr key={row.observation_date}>
                      <th scope="row" className="px-3 py-2 text-left font-normal">
                        {formatMacroMonth(row.observation_date, locale)}
                      </th>
                      <td className={NUM_CELL}>
                        {formatMacroValue(row.first_release_value, "level", locale)}
                      </td>
                      <td className={NUM_CELL}>
                        {formatMacroValue(row.latest_value, "level", locale)}
                      </td>
                      <td className={`${NUM_CELL} text-[var(--text-muted)]`}>
                        {formatMacroChange(row.revision, "level", locale)}
                      </td>
                      <td className={`${NUM_CELL} text-[var(--text-muted)]`}>
                        {Math.max(row.vintages.length, 1)}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
          <p className="text-[12px] text-[var(--text-muted)]">{query.data.first_release_note}</p>
        </>
      ) : null}
    </section>
  );
}
