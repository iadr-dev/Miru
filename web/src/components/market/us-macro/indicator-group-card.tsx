"use client";

import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useState } from "react";
import { IndicatorRow } from "@/components/market/us-macro/indicator-row";
import { ExpandModal } from "@/components/ui/expand-modal";
import { cn } from "@/lib/cn";
import { GROUP_CHART_COLOR } from "@/lib/us-macro-chart";
import type { DashboardPanel, DashboardSeries } from "@/types/market/us-macro";

/** Rows every category card shows before the rest move into the expand modal. Every card in the
 * grid reserves exactly this many rows of `--macro-row-height`, so a 3-row category and an
 * 11-row one are the same height. */
export const US_MACRO_COLLAPSED_ROW_COUNT = 5;

const LIST_STYLE = {
  minHeight: `calc(var(--macro-row-height) * ${US_MACRO_COLLAPSED_ROW_COUNT})`,
};

/** One macro group (inflation, labor, …) as a card of indicator rows. Each group draws its
 * sparklines in one categorical chart colour. The card body is a fixed height; categories with
 * more rows than the cap show an expand button that opens every row in the shared `ExpandModal`. */
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
  const [expanded, setExpanded] = useState(false);
  const color = GROUP_CHART_COLOR[panel.group];
  const groupName = t(`groups.${panel.group}`);
  const total = panel.series.length;
  const hasMore = total > US_MACRO_COLLAPSED_ROW_COUNT;

  // The series dialog is its own modal: close this one first so only one trap and one Esc
  // handler are live at a time.
  const openFromModal = useCallback(
    (series: DashboardSeries) => {
      setExpanded(false);
      onOpenSeries(series);
    },
    [onOpenSeries],
  );

  return (
    <section
      aria-labelledby={`us-macro-group-${panel.group}`}
      data-equal-height-card=""
      className="surface-card flex min-w-0 flex-col p-4 sm:p-5"
    >
      <h3
        id={`us-macro-group-${panel.group}`}
        className="font-semibold text-[15px] text-[var(--text)]"
      >
        {groupName}
      </h3>
      <p className="mt-0.5 mb-2 min-h-[2lh] text-[12px] text-[var(--text-muted)]">
        {t(`groupDescriptions.${panel.group}`)}
      </p>
      {total === 0 ? (
        <p style={LIST_STYLE} className="py-4 text-[13px] text-[var(--text-muted)]">
          {t("groupEmpty")}
        </p>
      ) : (
        <GroupRows
          series={panel.series.slice(0, US_MACRO_COLLAPSED_ROW_COUNT)}
          color={color}
          asOf={asOf}
          onOpen={onOpenSeries}
          fixedHeight
        />
      )}
      <div className="mt-2.5 min-h-11">
        {hasMore ? (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            aria-haspopup="dialog"
            aria-label={t("expandLabel", { count: total, group: groupName })}
            className={cn(
              "flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
              "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
              "focus-visible:outline-2 focus-visible:outline-[var(--accent)] focus-visible:outline-offset-2",
            )}
          >
            <span>{t("expandTo", { count: total })}</span>
          </button>
        ) : null}
      </div>
      <ExpandModal
        open={expanded}
        title={groupName}
        closeLabel={t("detail.close")}
        onClose={() => setExpanded(false)}
      >
        <GroupRows series={panel.series} color={color} asOf={asOf} onOpen={openFromModal} />
      </ExpandModal>
    </section>
  );
}

function GroupRows({
  series,
  color,
  asOf,
  onOpen,
  fixedHeight = false,
}: {
  readonly series: readonly DashboardSeries[];
  readonly color: string;
  readonly asOf: string | null;
  readonly onOpen: (series: DashboardSeries) => void;
  readonly fixedHeight?: boolean | undefined;
}): ReactNode {
  return (
    <ul
      style={fixedHeight ? LIST_STYLE : undefined}
      className={cn(
        "-mx-2 flex flex-col divide-y divide-[var(--border)]",
        fixedHeight &&
          "[&>li>button]:h-full [&>li>button]:justify-center [&>li]:h-[var(--macro-row-height)]",
      )}
    >
      {series.map((item) => (
        <IndicatorRow
          key={`${item.series_id}-${item.units}`}
          series={item}
          color={color}
          asOf={asOf}
          onOpen={onOpen}
        />
      ))}
    </ul>
  );
}
