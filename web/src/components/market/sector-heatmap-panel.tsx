"use client";

import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { type ReactNode, useState } from "react";
import { Caveat } from "@/app/[locale]/(app)/chat/caveat";
import { QueryState } from "@/app/[locale]/(app)/market/market-panel";
import { CardSkeletonBody } from "@/components/market/card-skeleton";
import { SectorHeatmapTreemap } from "@/components/market/sector-heatmap-treemap";
import { getSectorHeatmap } from "@/lib/api/market";
import type { Market } from "@/types/settings";

/**
 * 產業熱力圖 — public-tier `get_sector_heatmap` rendered as a treemap: tile size = trading-value
 * turnover (or stock count, when turnover-weighting wasn't available), tile color = avg
 * close-to-close % change on the `--up`/`--down` scale. Selecting a tile is local UI state only
 * (no drill-down endpoint exists yet — see the heatmap-tab design note); it just highlights the
 * tile so a future per-stock breakout has a selection to hang off of.
 */
export function SectorHeatmapPanel({ market }: { readonly market: Market }): ReactNode {
  const t = useTranslations("market.cards.sectorHeatmap");
  const tCaveat = useTranslations("chat.caveat");
  const [selected, setSelected] = useState<string | null>(null);

  const query = useQuery({
    queryKey: ["sector_heatmap", market],
    queryFn: () => getSectorHeatmap(market),
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
            />
          ) : (
            <p className="text-[13px] text-[var(--text-muted)]">
              {data.unavailable_reason ?? t("unavailable")}
            </p>
          )
        }
      </QueryState>
      <Caveat text={tCaveat("nonTrading")} />
    </section>
  );
}
