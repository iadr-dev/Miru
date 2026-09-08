"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SectorConstituentsTable } from "@/components/market/sector-constituents-table";
import { SectorHeatmapTreemap } from "@/components/market/sector-heatmap-treemap";
import { ExpandModal } from "@/components/ui/expand-modal";
import { getSectorConstituents, getSectorHeatmap } from "@/lib/api/market";
import type { Market } from "@/types/settings";

/**
 * 產業熱力圖 — public-tier `get_sector_heatmap` rendered as a treemap: tile size = trading-value
 * turnover (or stock count, when turnover-weighting wasn't available), tile color = avg
 * close-to-close % change on the `--up`/`--down` scale. Selecting a tile opens the per-stock
 * drill-down (`get_sector_constituents`) for that industry in an `ExpandModal` — the query only
 * fires once an industry is actually selected (`enabled: selected !== null`), never a background
 * fetch for every tile up front.
 */
export function SectorHeatmapPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const [selected, setSelected] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["sector_heatmap", market],
    queryFn: () => getSectorHeatmap(market),
  });

  const constituentsQuery = useQuery({
    queryKey: ["sector_constituents", market, selected],
    queryFn: () => getSectorConstituents(market, selected ?? undefined),
    enabled: selected !== null,
  });

  return (
    <section className="flex w-full flex-col rounded-[var(--radius-card)] border border-[var(--border)] bg-[var(--surface)] p-5">
      <h2 className="mb-3 font-semibold text-[15px] text-[var(--text)]">{t("title")}</h2>
      <QueryState query={query} skeleton={<CardSkeletonBody height={360} />}>
        {(data) =>
          data.data_available ? (
            <SectorHeatmapTreemap
              rows={data.industries}
              selected={selected}
              onSelect={setSelected}
              updatedAt={query.dataUpdatedAt}
            />
          ) : (
            <p className="text-[13px] text-[var(--text-muted)]">
              {data.unavailable_reason ?? t("unavailable")}
            </p>
          )
        }
      </QueryState>

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
