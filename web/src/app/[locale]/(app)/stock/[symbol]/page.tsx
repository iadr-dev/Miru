import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { AssetHeader } from "@/components/stock/asset-header";
import { StockTabBar } from "@/components/stock/stock-tab-bar";
import type { Market } from "@/types/settings";

const DEFAULT_MARKET: Market = "TW";

/** The stock-detail page (`/stock/{symbol}`, `docs/web/stock-detail.md`). Phase 1 of the
 * approved plan: AssetHeader + 3 real tabs (基本資料/財務分析/籌碼分析); the other 6 tabs render
 * a calm "尚未支援" state via `StockTabBar` rather than being hidden or faked. `market` defaults
 * to TW (this repo's only fully-live market for the stock-detail tools) and can be overridden
 * via `?market=US`, same pattern the `/market` page's own `DEFAULT_MARKET` establishes. */
export default async function StockDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ locale: string; symbol: string }>;
  searchParams: Promise<{ market?: string }>;
}): Promise<ReactNode> {
  const { locale, symbol } = await params;
  const { market: marketParam } = await searchParams;
  setRequestLocale(locale);
  const market: Market = marketParam === "US" ? "US" : DEFAULT_MARKET;

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <AssetHeader symbol={symbol} market={market} />
      <StockTabBar symbol={symbol} market={market} />
    </div>
  );
}
