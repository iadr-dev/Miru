"use client";

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { EtfTabContent } from "@/components/market/etf-tab-content";
import { MarketTabPanels } from "@/components/market/market-tabs";
import { TwTabContent } from "@/components/market/tw-tab-content";
import { UsTabContent } from "@/components/market/us-tab-content";
import { useDataUpdates } from "@/hooks/use-data-updates";
import type { Market } from "@/types/settings";

const DEFAULT_MARKET: Market = "TW";
const US_MARKET: Market = "US";

export function MarketPanel(): ReactNode {
  useDataUpdates();

  return (
    <div className="p-6">
      <MarketTabPanels
        renderTw={() => <TwTabContent market={DEFAULT_MARKET} />}
        renderUs={() => <UsTabContent market={US_MARKET} />}
        renderEtf={() => <EtfTabContent market={DEFAULT_MARKET} />}
      />
    </div>
  );
}
