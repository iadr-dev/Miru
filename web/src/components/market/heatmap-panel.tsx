"use client";

import type { ReactNode } from "react";
import { RainbowBorderCard } from "@/components/market/rainbow-border-card";
import { SectorHeatmapPanel } from "@/components/market/sector-heatmap-panel";
import type { Market } from "@/types/settings";

/**
 * 熱力圖 — the TW tab's 4th sub-tab: 產業熱力圖 (public sector-heatmap treemap, with a 板塊輪動
 * rank-bump-chart toggle, and the per-stock 漲跌 drill-down). 產業鏈資金流向 previously lived
 * here but moved to `ChipDataPanel` (籌碼 sub-tab) — it's a chip/institutional-flow read, the
 * same family as that sub-tab's other cards, not a heatmap-shaped visual.
 */
export function HeatmapPanel({ market }: { readonly market: Market }): ReactNode {
  return (
    <div className="flex flex-col gap-4">
      <RainbowBorderCard>
        <SectorHeatmapPanel market={market} />
      </RainbowBorderCard>
    </div>
  );
}
