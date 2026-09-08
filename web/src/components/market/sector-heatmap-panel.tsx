"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SectorConstituentsTable } from "@/components/market/sector-constituents-table";
import { SectorHeatmapTreemap } from "@/components/market/sector-heatmap-treemap";
import { SectorRotationBumpChart } from "@/components/market/sector-rotation-bump-chart";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getSectorConstituents, getSectorHeatmap, getSectorRotation } from "@/lib/api/market";
import { cn } from "@/lib/cn";
import {
  SECTOR_HEATMAP_PERIODS,
  SECTOR_HEATMAP_SIZE_BY_OPTIONS,
  type SectorHeatmapPeriod,
  type SectorHeatmapSizeBy,
} from "@/types/market";
import type { Market } from "@/types/settings";

type SectorView = "heatmap" | "rotation";
const SECTOR_VIEWS = ["heatmap", "rotation"] as const satisfies readonly SectorView[];

/**
 * 產業熱力圖 / 板塊輪動 — a view toggle over two representations of the same industry universe:
 * "heatmap" is `get_sector_heatmap` rendered as a treemap (tile size = `sizeBy`, color =
 * avg_pct_change over `period`); "rotation" is `get_sector_rotation` rendered as a rank-bump
 * chart (one line per industry, Y = daily rank by net institutional flow over the last ~10
 * trading days — `period`/`sizeBy` only apply to the heatmap view, rotation has no equivalent
 * selectors). Each view's own query only runs while it's the active view
 * (`enabled: view === "..."`), so switching never fires both fetches up front.
 *
 * `period`/`sizeBy` mirror the reference 熱力圖 app's own 漲跌績效/排序方式 selectors — see
 * `get_sector_heatmap`'s own docstring for why "immediate" (單日) is the default and every other
 * period is a trading-day lookback, never a calendar-day one.
 *
 * Selecting a heatmap tile opens the per-stock drill-down (`get_sector_constituents`) for that
 * industry in an `ExpandModal` — the query only fires once an industry is actually selected.
 */
export function SectorHeatmapPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const [view, setView] = useState<SectorView>("heatmap");
  const [period, setPeriod] = useState<SectorHeatmapPeriod>("immediate");
  const [sizeBy, setSizeBy] = useState<SectorHeatmapSizeBy>("turnover");
  const [selected, setSelected] = useState<string | null>(null);

  const heatmapQuery = useQuery({
    queryKey: ["sector_heatmap", market, period, sizeBy],
    queryFn: () => getSectorHeatmap(market, undefined, period, sizeBy),
    enabled: view === "heatmap",
  });

  const rotationQuery = useQuery({
    queryKey: ["sector_rotation", market],
    queryFn: () => getSectorRotation(market),
    enabled: view === "rotation",
  });

  const constituentsQuery = useQuery({
    queryKey: ["sector_constituents", market, selected],
    queryFn: () => getSectorConstituents(market, selected ?? undefined),
    enabled: selected !== null,
  });

  return (
    <section
      aria-label={view === "heatmap" ? t("title") : t("rotationTitle")}
      className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5"
    >
      <div className="mb-3 flex flex-wrap items-center justify-end gap-3">
        <div
          role="group"
          aria-label={t("viewToggleLabel")}
          className="inline-flex gap-0.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]"
        >
          {SECTOR_VIEWS.map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              aria-pressed={view === v}
              className={cn(
                "rounded-[var(--radius-pill)] px-3 py-1 text-[12px] transition-colors",
                view === v
                  ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
                  : "text-[var(--text-muted)] hover:text-[var(--text)]",
              )}
            >
              {t(v === "heatmap" ? "viewHeatmap" : "viewRotation")}
            </button>
          ))}
        </div>
      </div>

      {view === "heatmap" ? (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-4 text-[12px]">
            <label className="flex items-center gap-1.5 text-[var(--text-muted)]">
              {t("sizeByLabel")}
              <select
                value={sizeBy}
                onChange={(event) => setSizeBy(event.target.value as SectorHeatmapSizeBy)}
                className={cn(
                  "rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)]",
                  "px-2 py-1 text-[12px] text-[var(--text)]",
                  "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                )}
              >
                {SECTOR_HEATMAP_SIZE_BY_OPTIONS.map((option) => (
                  <option key={option} value={option}>
                    {t(`sizeBy.${option}`)}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1.5 text-[var(--text-muted)]">
              {t("periodLabel")}
              <select
                value={period}
                onChange={(event) => setPeriod(event.target.value as SectorHeatmapPeriod)}
                className={cn(
                  "rounded-[var(--radius-input)] border border-[var(--border)] bg-[var(--surface-2)]",
                  "px-2 py-1 text-[12px] text-[var(--text)]",
                  "!outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent)]",
                )}
              >
                {SECTOR_HEATMAP_PERIODS.map((option) => (
                  <option key={option} value={option}>
                    {t(`period.${option}`)}
                  </option>
                ))}
              </select>
            </label>
          </div>

          <QueryState query={heatmapQuery} skeleton={<CardSkeletonBody height={360} />}>
            {(data) =>
              data.data_available ? (
                <SectorHeatmapTreemap
                  rows={data.industries}
                  sizeBy={data.size_by}
                  selected={selected}
                  onSelect={setSelected}
                  updatedAt={heatmapQuery.dataUpdatedAt}
                />
              ) : (
                <p className="text-[13px] text-[var(--text-muted)]">
                  {data.unavailable_reason ?? t("unavailable")}
                </p>
              )
            }
          </QueryState>
        </>
      ) : (
        <QueryState query={rotationQuery} skeleton={<CardSkeletonBody height={360} />}>
          {(data) =>
            data.data_available ? (
              <SectorRotationBumpChart rows={data.rows} tradeDates={data.trade_dates} />
            ) : (
              <p className="text-[13px] text-[var(--text-muted)]">
                {data.unavailable_reason ?? t("rotationUnavailable")}
              </p>
            )
          }
        </QueryState>
      )}

      <ExpandModal
        open={selected !== null}
        title={selected ?? ""}
        closeLabel={t("collapse")}
        onClose={() => setSelected(null)}
      >
        <QueryState query={constituentsQuery} skeleton={<CardSkeletonBody height={320} />}>
          {(constituents) =>
            constituents.data_available ? (
              <SectorConstituentsTable rows={constituents.rows} />
            ) : (
              <p className="text-[13px] text-[var(--text-muted)]">
                {constituents.unavailable_reason ?? t("constituentsEmpty")}
              </p>
            )
          }
        </QueryState>
      </ExpandModal>
    </section>
  );
}
