"use client";

import { ChartLineIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useTranslations } from "next-intl";
import type { ReactNode } from "react";
import { useSearchPaletteStore } from "@/components/search/search-palette-store";
import { Link } from "@/i18n/navigation";
import { cn } from "@/lib/cn";

/** A few large, widely-recognized TW symbols — real tickers a first-time visitor plausibly
 * already knows by name, not a fabricated "trending" list (this app has no real trending-stocks
 * capability yet). Purely a faster on-ramp into the search palette's own real TW-roster/US-search
 * results; every link is a genuine `/stock/{symbol}` route. */
const QUICK_PICKS = [
  { symbol: "2330", name: "台積電" },
  { symbol: "2317", name: "鴻海" },
  { symbol: "0050", name: "元大台灣50" },
  { symbol: "2454", name: "聯發科" },
] as const;

/**
 * `/stock` (no symbol) cold-start guide (mirrors
 * `chat/empty-state.tsx`'s own pattern). The stock-DETAIL route (`/stock/{symbol}`) is a fully
 * built multi-tab workbench — this index page has nothing of its own to show without a symbol, so
 * rather than the old static "尚未支援" `ComingSoon` placeholder (which was actively wrong once
 * `/stock/{symbol}` shipped: the destination IS real, it just needs a symbol), this guides the
 * user straight to the same `SearchPalette` the top-bar 搜尋股票 button opens.
 */
export function StockEmptyState(): ReactNode {
  const t = useTranslations("stock.empty");
  const setSearchOpen = useSearchPaletteStore((s) => s.setOpen);

  return (
    <div className="flex min-h-full flex-col items-center justify-center px-4 py-10 text-center">
      <div className="grid size-12 place-items-center rounded-[var(--radius-button)] bg-[var(--surface-2)]">
        <ChartLineIcon size={24} weight="regular" className="text-[var(--text-muted)]" />
      </div>
      <h1 className="mt-4 font-semibold text-2xl text-[var(--text)] tracking-tight">
        {t("heading")}
      </h1>
      <p className="mt-3 max-w-md text-[15px] text-[var(--text-muted)] leading-relaxed">
        {t("subheading")}
      </p>

      <button
        type="button"
        onClick={() => setSearchOpen(true)}
        className={cn(
          "mt-6 inline-flex min-h-11 items-center gap-2 rounded-[var(--radius-pill)] px-5",
          "bg-[var(--accent)] font-medium text-[15px] text-[var(--bg)] transition-opacity",
          "hover:opacity-90 active:scale-[0.98]",
        )}
      >
        <MagnifyingGlassIcon size={18} weight="regular" aria-hidden />
        {t("searchCta")}
      </button>

      <p className="mt-8 text-[13px] text-[var(--text-muted)]">{t("quickPicksLabel")}</p>
      <div className="mt-3 flex w-full max-w-xl flex-wrap justify-center gap-2">
        {QUICK_PICKS.map((pick) => (
          <Link
            key={pick.symbol}
            href={`/stock/${pick.symbol}?market=TW`}
            className={cn(
              "min-h-11 rounded-[var(--radius-pill)] border border-[var(--border)] bg-[var(--surface)] px-4",
              "inline-flex items-center text-[var(--text)] text-sm transition-colors",
              "hover:border-[var(--accent)]",
            )}
          >
            {pick.name}
            <span className="ml-1.5 font-num text-[var(--text-muted)] text-xs tabular-nums">
              {pick.symbol}
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
