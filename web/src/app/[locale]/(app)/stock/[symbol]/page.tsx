import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { EtfTabBar } from "@/components/etf/etf-tab-bar";
import { AssetHeader } from "@/components/stock/asset-header";
import { StockTabBar } from "@/components/stock/stock-tab-bar";
import { UsDetailTabRouter } from "@/components/us-stock/us-detail-tab-router";
import { classifySymbolType, isEtfSymbolType } from "@/lib/symbol-type";
import type { Market } from "@/types/settings";

const DEFAULT_MARKET: Market = "TW";

/** The stock/ETF-detail page (`/stock/{symbol}`, `docs/web/stock-detail.md`) — one route for
 * both asset kinds (every existing symbol link, including `etf-list-table.tsx`'s, already points
 * here via `SymbolLink`). `classifySymbolType` is a pure code-shape check (no fetch), so TW
 * branches synchronously between `StockTabBar` (stock) and `EtfTabBar` (etf/etf_leveraged/
 * etf_inverse) without adding latency. US has no such code-shape rule (`classifySymbolType`
 * always returns `"stock"` for US — see its own docstring), so `UsDetailTabRouter` picks
 * `UsStockTabBar` vs `UsEtfTabBar` client-side once it resolves whether the symbol is a fund.
 * `market` defaults to TW (this repo's only fully-live market for the stock-detail tools) and
 * can be overridden via `?market=US`, same pattern the `/market` page's own `DEFAULT_MARKET`
 * establishes. */
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
  const symbolType = classifySymbolType(symbol);

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <AssetHeader symbol={symbol} market={market} symbolType={symbolType} />
      {market === "US" ? (
        <UsDetailTabRouter symbol={symbol} market={market} />
      ) : isEtfSymbolType(symbolType) ? (
        <EtfTabBar symbol={symbol} market={market} symbolType={symbolType} />
      ) : (
        <StockTabBar symbol={symbol} market={market} />
      )}
    </div>
  );
}
