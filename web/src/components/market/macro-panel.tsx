"use client";

import type { ReactNode } from "react";
import { MacroCalendarPanel } from "@/components/market/macro-calendar-panel";
import { MacroIndicatorsPanel } from "@/components/market/macro-indicators-panel";
import { EconomicReleasesFeed } from "@/components/market/us-macro/economic-releases-feed";
import { UsMacroSection } from "@/components/market/us-macro/us-macro-section";

/** 總經 sub-tab — stacks the macro-indicators card (yield/Fed-rate/gold/oil, Sponsor-gated at
 * the tool level), the 美國經濟數據 section (yield curve + indicator groups, public), and the
 * macro-release calendar (public, financecalendar.com) with the 經濟數據發布 feed right under it.
 * No shared grid-cell height constraint here (unlike 每日焦點's 2x2 grid) since this is its own
 * sub-tab, so a simple vertical stack reads better than forcing both into equal-height cells. */
export function MacroPanel(): ReactNode {
  return (
    <div className="flex flex-col gap-4">
      <MacroIndicatorsPanel />
      <UsMacroSection />
      <MacroCalendarPanel />
      <EconomicReleasesFeed />
    </div>
  );
}
