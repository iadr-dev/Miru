"use client";

import type { ReactNode } from "react";
import { Link } from "@/i18n/navigation";
import type { Market } from "@/types/settings";

/** The one place every stock/ETF list row across the market/ETF tabs (AI 多空排行, 漲跌幅排行,
 * ETF 列表, 今日訊號) links to the real `/stock/{symbol}` detail page
 * (`web/src/app/[locale]/(app)/stock/[symbol]/page.tsx`) — one route-building convention so a
 * future URL-shape change (e.g. a query-param rename) only needs to land here.
 *
 * Wraps its children rather than re-rendering the name/ticker markup itself, since every calling
 * row already has its own name+ticker layout (varying className/truncation per table) — this
 * only owns the navigation behavior, never the visual shape. `stopPropagation` matters wherever
 * a caller's own row-level `onClick` exists (none currently do, but this keeps future callers
 * from double-handling the click); `aria-hidden` on the wrapped content stays with the caller. */
export function SymbolLink({
  symbol,
  market,
  className,
  children,
}: {
  readonly symbol: string;
  readonly market: Market;
  readonly className?: string;
  readonly children: ReactNode;
}): ReactNode {
  return (
    <Link
      href={`/stock/${encodeURIComponent(symbol)}?market=${market}`}
      className={className}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </Link>
  );
}
