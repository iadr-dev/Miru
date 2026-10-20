import { setRequestLocale } from "next-intl/server";
import type { ReactNode } from "react";
import { StockEmptyState } from "./stock-empty-state";

/** `/stock` (no symbol) — the stock-DETAIL route (`/stock/{symbol}`) is a fully built multi-tab
 * workbench, so this index is a guide into it (search palette + a few quick-pick symbols), not a
 * "coming soon" stub — see `stock-empty-state.tsx`'s own docstring for why that placeholder was
 * retired here specifically. */
export default async function StockPage({
  params,
}: Readonly<{
  params: Promise<{ locale: string }>;
}>): Promise<ReactNode> {
  const { locale } = await params;
  setRequestLocale(locale);
  return <StockEmptyState />;
}
