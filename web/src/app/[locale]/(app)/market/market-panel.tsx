"use client";

import type { ReactNode } from "react";
import { EtfTabContent } from "@/components/market/etf-tab-content";
import { MarketTabPanels } from "@/components/market/market-tabs";
import { TwTabContent } from "@/components/market/tw-tab-content";
import { UsTabContent } from "@/components/market/us-tab-content";
import { SectionBoundary } from "@/components/ui/panel-error-boundary";
import { useDataUpdates } from "@/hooks/use-data-updates";
import type { Market } from "@/types/settings";

const DEFAULT_MARKET: Market = "TW";
const US_MARKET: Market = "US";

export function MarketPanel(): ReactNode {
  useDataUpdates();

  return (
    <div className="p-6">
      <MarketTabPanels
        renderTw={() => (
          <SectionBoundary resetKeys={["TW"]}>
            <TwTabContent market={DEFAULT_MARKET} />
          </SectionBoundary>
        )}
        renderUs={() => (
          <SectionBoundary resetKeys={["US"]}>
            <UsTabContent market={US_MARKET} />
          </SectionBoundary>
        )}
        renderEtf={() => (
          <SectionBoundary resetKeys={["ETF"]}>
            <EtfTabContent market={DEFAULT_MARKET} />
          </SectionBoundary>
        )}
      />
    </div>
  );
}
