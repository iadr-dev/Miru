import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { MarketPanel } from "./market-panel";

/** Market landing (the product default: `/` → `/market`). Ships only the 6 real data blocks
 * `backend/src/api/routers/market_rest.py` actually backs today — see that router's own module
 * docstring for the 4 tools deliberately omitted rather than faked. */
export default async function MarketPage({
  params,
}: {
  params: Promise<{ locale: string }>;
}): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <MarketPanel />;
}
