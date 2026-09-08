"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SectorConstituentsTable } from "@/components/market/sector-constituents-table";
import { SectorHeatmapTable } from "@/components/market/sector-heatmap-table";
import { SectorHeatmapTreemap } from "@/components/market/sector-heatmap-treemap";
import { SectorRotationBarChart } from "@/components/market/sector-rotation-bar-chart";
import { SectorRotationParticleSphere } from "@/components/market/sector-rotation-particle-sphere";
import { StockHeatmapTable } from "@/components/market/stock-heatmap-table";
import { StockHeatmapTreemap } from "@/components/market/stock-heatmap-treemap";
import { ExpandModal } from "@/components/ui/expand-modal";
import {
  getSectorConstituents,
  getSectorHeatmap,
  getSectorRotation,
  getStockHeatmap,
} from "@/lib/api/market";
import { cn } from "@/lib/cn";
import {
  SECTOR_HEATMAP_PERIODS,
  SECTOR_HEATMAP_SIZE_BY_OPTIONS,
  type SectorHeatmapPeriod,
  type SectorHeatmapSizeBy,
} from "@/types/market";
import type { Market } from "@/types/settings";

type SectorView = "heatmap" | "stock" | "rotation";
const SECTOR_VIEWS = ["heatmap", "stock", "rotation"] as const satisfies readonly SectorView[];

// Both treemaps collapse to a fixed count inline — showing every row (45+ industries, 150
// stocks) in the same fixed-aspect-ratio box squeezes tiles down to unreadable slivers, the
// exact problem `StockHeatmapTreemap`'s own docstring documents fixing for the stock view. Same
// "展開完整 N 筆" `ExpandModal` convention every other ranked-list market card in this dashboard
// uses — the collapsed view is a readability cut, never a data-completeness one.
const INDUSTRY_HEATMAP_COLLAPSED_COUNT = 24;
// `StockHeatmapTreemap` is now a NESTED treemap (industry region -> top-2 stocks inside it, see
// that component's own docstring for why a flat per-stock ranking kept producing thin tiles) —
// this caps how many industry REGIONS are shown inline, same collapsed-view readability cut
// `INDUSTRY_HEATMAP_COLLAPSED_COUNT` applies to the industry view, just passed as `maxIndustries`
// rather than a row slice (the grouping-by-industry has to happen inside the component, since a
// stock-row slice could all land in the same 2-3 industries).
const STOCK_HEATMAP_COLLAPSED_INDUSTRIES = 24;

/** 板塊輪動's own 2-way toggle between its bar-chart and particle-sphere visualizations — a rank
 * bump-chart previously lived here too but was dropped entirely (not kept as a 3rd option), so
 * this is intentionally just these two. */
type RotationVisualization = "bar" | "sphere";
const ROTATION_VISUALIZATIONS = [
  "bar",
  "sphere",
] as const satisfies readonly RotationVisualization[];

/**
 * 產業熱力圖 / 股票熱力圖 / 板塊輪動 — a 3-way view toggle over the same industry/stock universe:
 * "heatmap" is `get_sector_heatmap` rendered as a per-industry treemap (tile size = `sizeBy`,
 * color = avg_pct_change over `period`); "stock" is `get_stock_heatmap` rendered as a flat
 * per-stock treemap (finviz's own individual-ticker map, top-N ranked by the same `sizeBy`
 * metric, sharing the exact same period/sizeBy selectors as the industry view); "rotation" is
 * `get_sector_rotation` rendered as EITHER a bar chart or a particle sphere
 * (`rotationVisualization`, its own inner toggle — both consume the exact same
 * `SectorRotationDayRow[]`/`trade_dates` data, only the visualization differs). `period`/`sizeBy`
 * apply to BOTH the heatmap and stock views (rotation has no equivalent selectors). Each
 * top-level view's own query only runs while it's the active view (`enabled: view === "..."`), so
 * switching never fires all three fetches up front.
 *
 * `period`/`sizeBy` mirror the reference 熱力圖 app's own 漲跌績效/排序方式 selectors — see
 * `get_sector_heatmap`'s own docstring for why "immediate" (單日) is the default and every other
 * period is a trading-day lookback, never a calendar-day one.
 *
 * Selecting a heatmap tile opens the per-stock drill-down (`get_sector_constituents`) for that
 * industry in an `ExpandModal` — the query only fires once an industry is actually selected. The
 * stock view has no equivalent drill-down: a stock tile is already the leaf.
 */
export function SectorHeatmapPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const [view, setView] = useState<SectorView>("heatmap");
  const [rotationVisualization, setRotationVisualization] = useState<RotationVisualization>("bar");
  const [period, setPeriod] = useState<SectorHeatmapPeriod>("immediate");
  const [sizeBy, setSizeBy] = useState<SectorHeatmapSizeBy>("turnover");
  const [selected, setSelected] = useState<string | null>(null);
  const [heatmapExpanded, setHeatmapExpanded] = useState(false);
  const [stockHeatmapExpanded, setStockHeatmapExpanded] = useState(false);

  const heatmapQuery = useQuery({
    queryKey: ["sector_heatmap", market, period, sizeBy],
    queryFn: () => getSectorHeatmap(market, undefined, period, sizeBy),
    enabled: view === "heatmap",
  });

  const stockHeatmapQuery = useQuery({
    queryKey: ["stock_heatmap", market, period, sizeBy],
    queryFn: () => getStockHeatmap(market, undefined, period, sizeBy),
    enabled: view === "stock",
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

  const viewTitle =
    view === "heatmap" ? t("title") : view === "stock" ? t("viewStockHeatmap") : t("rotationTitle");

  return (
    <section
      aria-label={viewTitle}
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
              {t(
                v === "heatmap"
                  ? "viewHeatmap"
                  : v === "stock"
                    ? "viewStockHeatmap"
                    : "viewRotation",
              )}
            </button>
          ))}
        </div>
      </div>

      {view === "heatmap" || view === "stock" ? (
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

          {view === "heatmap" ? (
            <QueryState query={heatmapQuery} skeleton={<CardSkeletonBody height={360} />}>
              {(data) =>
                data.data_available ? (
                  <>
                    <SectorHeatmapTreemap
                      rows={data.industries.slice(0, INDUSTRY_HEATMAP_COLLAPSED_COUNT)}
                      sizeBy={data.size_by}
                      selected={selected}
                      onSelect={setSelected}
                      updatedAt={heatmapQuery.dataUpdatedAt}
                    />
                    {data.industries.length > INDUSTRY_HEATMAP_COLLAPSED_COUNT ? (
                      <button
                        type="button"
                        onClick={() => setHeatmapExpanded(true)}
                        className={cn(
                          "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                          "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                        )}
                      >
                        <span>{t("expandTo", { count: data.industries.length })}</span>
                      </button>
                    ) : null}
                    <ExpandModal
                      open={heatmapExpanded}
                      title={t("title")}
                      closeLabel={t("collapse")}
                      onClose={() => setHeatmapExpanded(false)}
                    >
                      <SectorHeatmapTable rows={data.industries} />
                    </ExpandModal>
                  </>
                ) : (
                  <p className="text-[13px] text-[var(--text-muted)]">
                    {data.unavailable_reason ?? t("unavailable")}
                  </p>
                )
              }
            </QueryState>
          ) : (
            <QueryState query={stockHeatmapQuery} skeleton={<CardSkeletonBody height={360} />}>
              {(data) => {
                if (!data.data_available) {
                  return (
                    <p className="text-[13px] text-[var(--text-muted)]">
                      {data.unavailable_reason ?? t("unavailable")}
                    </p>
                  );
                }
                const industryCount = new Set(data.stocks.map((row) => row.industry)).size;
                return (
                  <>
                    <StockHeatmapTreemap
                      rows={data.stocks}
                      sizeBy={data.size_by}
                      maxIndustries={STOCK_HEATMAP_COLLAPSED_INDUSTRIES}
                      updatedAt={stockHeatmapQuery.dataUpdatedAt}
                    />
                    {industryCount > STOCK_HEATMAP_COLLAPSED_INDUSTRIES ? (
                      <button
                        type="button"
                        onClick={() => setStockHeatmapExpanded(true)}
                        className={cn(
                          "mt-2.5 flex min-h-11 w-full items-center justify-center gap-1.5 rounded-[var(--radius-input)]",
                          "bg-[var(--surface-2)] text-[12.5px] text-[var(--text-muted)] hover:bg-[var(--surface-3)] hover:text-[var(--text)]",
                        )}
                      >
                        <span>{t("expandTo", { count: data.stocks.length })}</span>
                      </button>
                    ) : null}
                    <ExpandModal
                      open={stockHeatmapExpanded}
                      title={t("viewStockHeatmap")}
                      closeLabel={t("collapse")}
                      onClose={() => setStockHeatmapExpanded(false)}
                    >
                      <StockHeatmapTable rows={data.stocks} />
                    </ExpandModal>
                  </>
                );
              }}
            </QueryState>
          )}
        </>
      ) : (
        <>
          <div className="mb-3 flex justify-start">
            <div
              role="group"
              aria-label={t("rotationViewToggleLabel")}
              className="inline-flex gap-0.5 rounded-[var(--radius-pill)] bg-[var(--surface-2)] p-[3px]"
            >
              {ROTATION_VISUALIZATIONS.map((viz) => (
                <button
                  key={viz}
                  type="button"
                  onClick={() => setRotationVisualization(viz)}
                  aria-pressed={rotationVisualization === viz}
                  className={cn(
                    "rounded-[var(--radius-pill)] px-3 py-1 text-[12px] transition-colors",
                    rotationVisualization === viz
                      ? "bg-[var(--accent)] font-semibold text-[color:#06231F]"
                      : "text-[var(--text-muted)] hover:text-[var(--text)]",
                  )}
                >
                  {t(viz === "bar" ? "rotationViewBar" : "rotationViewSphere")}
                </button>
              ))}
            </div>
          </div>
          <QueryState query={rotationQuery} skeleton={<CardSkeletonBody height={360} />}>
            {(data) =>
              data.data_available ? (
                rotationVisualization === "bar" ? (
                  <SectorRotationBarChart rows={data.rows} tradeDates={data.trade_dates} />
                ) : (
                  <SectorRotationParticleSphere rows={data.rows} tradeDates={data.trade_dates} />
                )
              ) : (
                <p className="text-[13px] text-[var(--text-muted)]">
                  {data.unavailable_reason ?? t("rotationUnavailable")}
                </p>
              )
            }
          </QueryState>
        </>
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
