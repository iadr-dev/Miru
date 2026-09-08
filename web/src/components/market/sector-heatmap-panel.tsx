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
import type { Market } from "@/types/settings";

type SectorView = "heatmap" | "rotation";
const SECTOR_VIEWS = ["heatmap", "rotation"] as const satisfies readonly SectorView[];

/**
 * 產業熱力圖 / 板塊輪動 — a view toggle over two representations of the same industry universe:
 * "heatmap" is `get_sector_heatmap` rendered as a treemap (tile size = turnover, color =
 * avg_pct_change); "rotation" is `get_sector_rotation` rendered as a rank-bump chart (one line
 * per industry, Y = daily rank by net institutional flow over the last ~10 trading days). Each
 * view's own query only runs while it's the active view (`enabled: view === "..."`), so
 * switching never fires both fetches up front.
 *
 * Selecting a heatmap tile opens the per-stock drill-down (`get_sector_constituents`) for that
 * industry in an `ExpandModal` — the query only fires once an industry is actually selected.
 */
export function SectorHeatmapPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const [view, setView] = useState<SectorView>("heatmap");
  const [selected, setSelected] = useState<string | null>(null);

  const heatmapQuery = useQuery({
    queryKey: ["sector_heatmap", market],
    queryFn: () => getSectorHeatmap(market),
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
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-semibold text-[15px] text-[var(--text)]">
          {view === "heatmap" ? t("title") : t("rotationTitle")}
        </h2>
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
        <QueryState query={heatmapQuery} skeleton={<CardSkeletonBody height={360} />}>
          {(data) =>
            data.data_available ? (
              <SectorHeatmapTreemap
                rows={data.industries}
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
