"use client";

import type { ReactNode } from "react";
import { MacroCalendarPanel } from "@/components/market/macro-calendar-panel";
import { MacroIndicatorsPanel } from "@/components/market/macro-indicators-panel";

/** 總經 sub-tab — stacks the macro-indicators card (yield/Fed-rate/gold/oil, Sponsor-gated at
 * the tool level) above the macro-release calendar (public, financecalendar.com). No shared
 * grid-cell height constraint here (unlike 每日焦點's 2x2 grid) since this is its own sub-tab, so
 * a simple vertical stack reads better than forcing both into equal-height cells. */
export function MacroPanel(): ReactNode {
  return (
    <div className="flex flex-col gap-4">
      <MacroIndicatorsPanel />
      <MacroCalendarPanel />
    </div>
  );
}
