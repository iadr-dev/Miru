"use client";

import {
  CaretDownIcon,
  CaretUpIcon,
  DownloadSimpleIcon,
  StarIcon,
} from "@phosphor-icons/react/dist/ssr";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { useTranslations } from "next-intl";
import { type ReactNode, useCallback, useMemo, useState } from "react";
import type { ScreenerColumn } from "@/components/screener/screener-market-config";
import { useSymbolQuotes } from "@/hooks/use-symbol-quotes";
import {
  groupsForAssetClass,
  useAddWatchlistItem,
  useUnstarSymbolEverywhere,
  useWatchlists,
} from "@/hooks/use-watchlists";
import { useRouter } from "@/i18n/navigation";
import { hashToChartToken } from "@/lib/chart-token-hash";
import { cn } from "@/lib/cn";
import type { FactorCatalog, ScreenerMarket, ScreenerSurvivor } from "@/types/screener";

type EnrichedRow = ScreenerSurvivor & {
  name: string | null;
  price: number | null;
  changePct: number | null;
  volume: number | null;
  industryCategory: string | null;
  peRatio: number | null;
  marketCap: number | null;
  pctHeldInstitutions: number | null;
  shortPctOfFloat: number | null;
};

function watchlistMarket(market: ScreenerMarket): "TW" | "US" {
  return market === "US" ? "US" : "TW";
}

function displayNumber(
  display: Record<string, number | string | boolean | null>,
  key: string,
): number | null {
  const value = display[key];
  return typeof value === "number" ? value : null;
}

function displayString(
  display: Record<string, number | string | boolean | null>,
  key: string,
): string | null {
  const value = display[key];
  return typeof value === "string" ? value : null;
}

/** Bounds how many rows this table enriches with `useSymbolQuotes` at once — a page's worth, not
 * the whole survivor list. `run_screener`'s own `size` can return up to 250 rows, and
 * `useSymbolQuotes` chunks into ≤20-symbol batches under the hood; enriching hundreds of rows in
 * one pass means dozens of concurrent batch requests plus a proportionally large re-render on
 * every parent state change (e.g. toggling a second 訊號 checkbox while the first enrichment is
 * still in flight), which is exactly what wedged a real browser tab during testing. Pagination
 * keeps the enrichment fan-out — and the render cost — bounded regardless of how many symbols
 * the funnel actually matched. 15/page everywhere this table is used (main TW/US/ETF screen and
 * the Themes tab both render through this same component). */
const PAGE_SIZE = 15;

function toCsv(rows: EnrichedRow[]): string {
  const header = [
    "symbol",
    "name",
    "price",
    "change_pct",
    "volume",
    "sector",
    "market_cap",
    "pe_ratio",
    "pct_held_institutions",
    "short_pct_of_float",
    "matched",
  ];
  const lines = rows.map((row) =>
    [
      row.symbol,
      row.name ?? "",
      row.price ?? "",
      row.changePct ?? "",
      row.volume ?? "",
      row.industryCategory ?? "",
      row.marketCap ?? "",
      row.peRatio ?? "",
      row.pctHeldInstitutions ?? "",
      row.shortPctOfFloat ?? "",
      row.matched_conditions.join(";"),
    ]
      .map((value) => `"${String(value).replaceAll('"', '""')}"`)
      .join(","),
  );
  return [header.join(","), ...lines].join("\n");
}

function downloadCsv(rows: EnrichedRow[]): void {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "screener-results.csv";
  link.click();
  URL.revokeObjectURL(url);
}

/** Prev/page-of-total/next — rendered above AND below the table (a 50-row page runs well past
 * the fold, so a control only at the top forces a scroll-back-up round-trip to advance).
 * Rendered unconditionally (visibility toggled, not mounted/unmounted) so a survivor count
 * crossing the pagination threshold never shifts every sibling in this flex row — the same
 * mount/unmount-under-live-data layout-shift bug already fixed once for the AND/OR toggle
 * (`condition-builder.tsx`); a control appearing/disappearing next to other clickable elements
 * moves them out from under an in-flight click, which is what wedges the tab. */
function PageNav({
  page,
  pageCount,
  onPageChange,
}: {
  readonly page: number;
  readonly pageCount: number;
  readonly onPageChange: (next: number) => void;
}): ReactNode {
  const hasMultiplePages = pageCount > 1;
  return (
    <div
      aria-hidden={!hasMultiplePages}
      className={cn("flex items-center gap-1", !hasMultiplePages && "invisible")}
    >
      <button
        type="button"
        tabIndex={hasMultiplePages ? undefined : -1}
        disabled={page === 0}
        onClick={() => onPageChange(Math.max(0, page - 1))}
        className="min-h-9 rounded-[var(--radius-pill)] border border-[var(--border)] px-2.5 text-[12px] text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40"
      >
        ‹
      </button>
      <span className="font-num text-[12px] text-[var(--text-muted)] tabular-nums">
        {page + 1} / {pageCount}
      </span>
      <button
        type="button"
        tabIndex={hasMultiplePages ? undefined : -1}
        disabled={page >= pageCount - 1}
        onClick={() => onPageChange(Math.min(pageCount - 1, page + 1))}
        className="min-h-9 rounded-[var(--radius-pill)] border border-[var(--border)] px-2.5 text-[12px] text-[var(--text-muted)] hover:text-[var(--text)] disabled:opacity-40"
      >
        ›
      </button>
    </div>
  );
}

/** The ranked survivors, TanStack Table (`web/CLAUDE.md`: "don't hand-roll data grids"), enriched
 * with real price/change%/volume/sector via `useSymbolQuotes` (the same batched ≤20-symbol read
 * the watchlist rail/leaderboards already use) rather than showing bare symbols. Sponsor columns
 * render a blurred 🔒 cell (README.md §5.2 馬賽克) — no value ever reaches this component for a
 * sponsor factor this pass. Composite quant score / PE / dividend yield are a follow-up (no
 * batched-by-arbitrary-symbol-list read exists yet for those — see PR notes). */
export function ScreenerTable({
  survivors,
  columns,
  market,
  catalog,
}: {
  readonly survivors: ScreenerSurvivor[];
  readonly columns: ScreenerColumn[];
  readonly market: ScreenerMarket;
  readonly catalog?: FactorCatalog;
}): ReactNode {
  const t = useTranslations("screener.table");
  const router = useRouter();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [page, setPage] = useState(0);
  const wlMarket = watchlistMarket(market);

  const conditionLabels = useMemo(() => {
    const labels = new Map<string, string>();
    for (const field of catalog?.fields ?? []) labels.set(field.key, field.label_zh);
    for (const signal of catalog?.signals ?? []) labels.set(signal.key, signal.label_zh);
    return labels;
  }, [catalog]);

  const pageCount = Math.max(1, Math.ceil(survivors.length / PAGE_SIZE));
  const clampedPage = Math.min(page, pageCount - 1);
  const pageSurvivors = useMemo(
    () => survivors.slice(clampedPage * PAGE_SIZE, (clampedPage + 1) * PAGE_SIZE),
    [survivors, clampedPage],
  );
  // US rows are enriched from `survivor.display` (Yahoo's screener response already carries
  // price/sector/etc. for every hit) — `useSymbolQuotes` stays TW/ETF-only, an empty symbol list
  // issues no request rather than a redundant lookup for data the survivor already has.
  const symbols = useMemo(
    () => (market === "US" ? [] : pageSurvivors.map((s) => s.symbol)),
    [pageSurvivors, market],
  );
  const { quotes } = useSymbolQuotes(symbols, wlMarket);
  const quoteBySymbol = useMemo(() => new Map(quotes.map((q) => [q.code, q])), [quotes]);

  const watchlistsQuery = useWatchlists(wlMarket);
  const addMutation = useAddWatchlistItem(wlMarket);
  const unstarMutation = useUnstarSymbolEverywhere(wlMarket);
  const groups = useMemo(
    () => groupsForAssetClass(watchlistsQuery.data ?? [], "stock"),
    [watchlistsQuery.data],
  );
  const starredSymbols = useMemo(
    () => new Set(groups.flatMap((view) => view.items.map((item) => item.symbol))),
    [groups],
  );
  const defaultGroupId = groups[0]?.group.id;

  const rows: EnrichedRow[] = useMemo(
    () =>
      pageSurvivors.map((survivor) => {
        if (market === "US") {
          const display = survivor.display;
          return {
            ...survivor,
            name: null,
            price: displayNumber(display, "regularMarketPrice"),
            changePct: displayNumber(display, "regularMarketChangePercent"),
            volume: displayNumber(display, "regularMarketVolume"),
            industryCategory: displayString(display, "sector"),
            peRatio: displayNumber(display, "peratio.lasttwelvemonths"),
            marketCap: displayNumber(display, "marketCap"),
            pctHeldInstitutions: displayNumber(display, "pctheldinst"),
            shortPctOfFloat: displayNumber(display, "short_percentage_of_float.value"),
          };
        }
        const quote = quoteBySymbol.get(survivor.symbol);
        return {
          ...survivor,
          name: quote?.name ?? null,
          price: quote?.price ?? null,
          changePct: quote?.changePct ?? null,
          volume: quote?.volume ?? null,
          industryCategory: quote?.industryCategory ?? null,
          peRatio: null,
          marketCap: null,
          pctHeldInstitutions: null,
          shortPctOfFloat: null,
        };
      }),
    [pageSurvivors, quoteBySymbol, market],
  );

  // Yahoo's screener response doesn't return every field for every symbol (confirmed live:
  // OTC/pink-sheet tickers commonly have no PE/institutional%/short-float% at all) — hiding a
  // column outright when the CURRENT PAGE has zero real values for it reads as "not tracked for
  // these results" rather than a wall of "—" that looks broken. Market cap is checked too since
  // it's just as capable of being universally absent for a given page.
  // Confirmed live against yfinance's screener response: Yahoo's screener quote payload never
  // includes `sector`/`industry` at all (checked real hits' full key lists) — US rows always
  // resolve `industryCategory` to null, so the column would be a wall of "—" if always shown.
  const hasAnySector = useMemo(() => rows.some((row) => row.industryCategory !== null), [rows]);
  const hasAnyMarketCap = useMemo(() => rows.some((row) => row.marketCap !== null), [rows]);
  const hasAnyPeRatio = useMemo(() => rows.some((row) => row.peRatio !== null), [rows]);
  const hasAnyPctHeldInstitutions = useMemo(
    () => rows.some((row) => row.pctHeldInstitutions !== null),
    [rows],
  );
  const hasAnyShortPctOfFloat = useMemo(
    () => rows.some((row) => row.shortPctOfFloat !== null),
    [rows],
  );

  const sectorColumn: ColumnDef<EnrichedRow> = useMemo(
    () => ({
      id: "sector",
      header: t("sector"),
      accessorFn: (row) => row.industryCategory ?? "",
      cell: (info) => {
        const category = info.row.original.industryCategory;
        if (!category) return null;
        const token = hashToChartToken(category);
        return (
          <span
            className="rounded-[var(--radius-pill)] px-2 py-0.5 text-[11px]"
            style={{
              backgroundColor: `color-mix(in srgb, ${token} 16%, transparent)`,
              color: token,
            }}
          >
            {category}
          </span>
        );
      },
    }),
    [t],
  );

  const usOnlyColumns: ColumnDef<EnrichedRow>[] = useMemo(() => {
    const columns: ColumnDef<EnrichedRow>[] = [];
    if (hasAnyMarketCap) {
      columns.push({
        id: "market_cap",
        header: t("marketCap"),
        accessorFn: (row) => row.marketCap ?? 0,
        cell: (info) =>
          info.row.original.marketCap !== null ? (
            <span className="font-num text-[var(--text-muted)] tabular-nums">
              {(info.row.original.marketCap / 1_000_000_000).toFixed(1)}B
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">—</span>
          ),
      });
    }
    if (hasAnyPeRatio) {
      columns.push({
        id: "pe_ratio",
        header: t("peRatio"),
        accessorFn: (row) => row.peRatio ?? Number.NEGATIVE_INFINITY,
        cell: (info) =>
          info.row.original.peRatio !== null ? (
            <span className="font-num text-[var(--text-muted)] tabular-nums">
              {info.row.original.peRatio.toFixed(1)}x
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">—</span>
          ),
      });
    }
    if (hasAnyPctHeldInstitutions) {
      columns.push({
        id: "pct_held_institutions",
        header: t("pctHeldInstitutions"),
        accessorFn: (row) => row.pctHeldInstitutions ?? Number.NEGATIVE_INFINITY,
        cell: (info) =>
          info.row.original.pctHeldInstitutions !== null ? (
            <span className="font-num text-[var(--text-muted)] tabular-nums">
              {info.row.original.pctHeldInstitutions.toFixed(1)}%
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">—</span>
          ),
      });
    }
    if (hasAnyShortPctOfFloat) {
      columns.push({
        id: "short_pct_of_float",
        header: t("shortPctOfFloat"),
        accessorFn: (row) => row.shortPctOfFloat ?? Number.NEGATIVE_INFINITY,
        cell: (info) =>
          info.row.original.shortPctOfFloat !== null ? (
            <span className="font-num text-[var(--text-muted)] tabular-nums">
              {info.row.original.shortPctOfFloat.toFixed(1)}%
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">—</span>
          ),
      });
    }
    return columns;
  }, [t, hasAnyMarketCap, hasAnyPeRatio, hasAnyPctHeldInstitutions, hasAnyShortPctOfFloat]);

  const toggleStar = useCallback(
    (symbol: string) => {
      const starred = starredSymbols.has(symbol);
      if (starred) {
        unstarMutation.mutate({ symbol, groups });
      } else if (defaultGroupId) {
        addMutation.mutate({ watchlistId: defaultGroupId, symbol });
      }
    },
    [starredSymbols, groups, defaultGroupId, unstarMutation, addMutation],
  );

  const columnDefs: ColumnDef<EnrichedRow>[] = useMemo(
    () => [
      {
        id: "star",
        header: "",
        cell: (info) => {
          const starred = starredSymbols.has(info.row.original.symbol);
          return (
            <button
              type="button"
              aria-pressed={starred}
              aria-label={t("star")}
              onClick={(event) => {
                event.stopPropagation();
                toggleStar(info.row.original.symbol);
              }}
              className="grid size-8 place-items-center rounded-[var(--radius-button)] text-[var(--text-muted)] hover:text-[var(--accent)]"
            >
              <StarIcon
                size={16}
                weight={starred ? "fill" : "regular"}
                className={starred ? "text-[var(--accent)]" : undefined}
              />
            </button>
          );
        },
        enableSorting: false,
      },
      {
        accessorKey: "symbol",
        header: t("symbol"),
        cell: (info) => (
          <div className="flex flex-col">
            <span className="font-medium text-[var(--text)]">{info.getValue<string>()}</span>
            {info.row.original.name ? (
              <span className="text-[11px] text-[var(--text-muted)]">{info.row.original.name}</span>
            ) : null}
          </div>
        ),
      },
      ...(hasAnySector ? [sectorColumn] : []),
      {
        id: "price",
        header: t("price"),
        accessorFn: (row) => row.price ?? Number.NEGATIVE_INFINITY,
        cell: (info) =>
          info.row.original.price !== null ? (
            <span className="font-num text-[var(--text)] tabular-nums">
              {info.row.original.price.toLocaleString()}
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">—</span>
          ),
      },
      {
        id: "change_pct",
        header: t("changePct"),
        accessorFn: (row) => row.changePct ?? Number.NEGATIVE_INFINITY,
        cell: (info) => {
          const value = info.row.original.changePct;
          if (value === null) return <span className="text-[var(--text-muted)]">—</span>;
          const up = value >= 0;
          return (
            <span
              role="group"
              className={cn(
                "inline-flex items-center gap-0.5 font-num tabular-nums",
                up ? "text-[var(--up)]" : "text-[var(--down)]",
              )}
              aria-label={`${up ? "上漲" : "下跌"} ${Math.abs(value).toFixed(2)}%`}
            >
              {up ? (
                <CaretUpIcon size={11} weight="fill" />
              ) : (
                <CaretDownIcon size={11} weight="fill" />
              )}
              {Math.abs(value).toFixed(2)}%
            </span>
          );
        },
      },
      {
        id: "volume",
        header: t("volume"),
        accessorFn: (row) => row.volume ?? 0,
        cell: (info) =>
          info.row.original.volume !== null ? (
            <span className="font-num text-[var(--text-muted)] tabular-nums">
              {info.row.original.volume.toLocaleString()}
            </span>
          ) : (
            <span className="text-[var(--text-muted)]">—</span>
          ),
      },
      ...(market === "US" ? usOnlyColumns : []),
      ...columns
        .filter((column) => column.sponsor)
        .map<ColumnDef<EnrichedRow>>((column) => ({
          id: column.key,
          header: `🔒 ${column.labelZh}`,
          cell: () => (
            <span className="rounded-[var(--radius-pill)] bg-[var(--surface-2)] px-2 py-1 text-[11px] text-[var(--text-muted)] backdrop-blur-sm">
              {t("sponsorLocked")}
            </span>
          ),
          enableSorting: false,
        })),
      {
        id: "matched_conditions",
        header: t("matchedConditions"),
        cell: (info) => (
          <div className="flex flex-wrap gap-1">
            {info.row.original.matched_conditions.map((condition) => (
              <span
                key={condition}
                className="rounded-[var(--radius-pill)] border border-[var(--accent)] px-2 py-0.5 text-[11px] text-[var(--accent)]"
              >
                {conditionLabels.get(condition) ?? condition}
              </span>
            ))}
          </div>
        ),
        enableSorting: false,
      },
    ],
    [
      t,
      columns,
      starredSymbols,
      toggleStar,
      conditionLabels,
      market,
      usOnlyColumns,
      hasAnySector,
      sectorColumn,
    ],
  );

  const table = useReactTable({
    data: rows,
    columns: columnDefs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="flex flex-col">
      <div className="flex items-center justify-between px-3 py-2">
        <span className="text-[12px] text-[var(--text-muted)]">
          {t("resultCount", { count: survivors.length })}
        </span>
        <div className="flex items-center gap-2">
          <PageNav page={clampedPage} pageCount={pageCount} onPageChange={setPage} />
          <button
            type="button"
            onClick={() => downloadCsv(rows)}
            className="inline-flex min-h-9 items-center gap-1.5 rounded-[var(--radius-pill)] border border-[var(--border)] px-3 text-[12px] text-[var(--text-muted)] hover:text-[var(--text)]"
          >
            <DownloadSimpleIcon size={13} weight="regular" />
            {t("exportCsv")}
          </button>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-[var(--surface-2)]">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    scope="col"
                    className="px-3 py-2 text-left font-medium text-[12px] text-[var(--text-muted)]"
                  >
                    {header.column.getCanSort() ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className={cn(
                          "flex min-h-9 items-center gap-1",
                          header.column.getIsSorted() && "text-[var(--accent)]",
                        )}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {header.column.getIsSorted() === "asc" ? "▲" : null}
                        {header.column.getIsSorted() === "desc" ? "▼" : null}
                      </button>
                    ) : (
                      flexRender(header.column.columnDef.header, header.getContext())
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-[var(--border)]">
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                tabIndex={0}
                onClick={() => router.push(`/stock/${row.original.symbol}`)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") router.push(`/stock/${row.original.symbol}`);
                }}
                className="cursor-pointer transition-colors hover:bg-[var(--surface-2)] focus-visible:outline-2 focus-visible:outline-[var(--accent)]"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {pageCount > 1 ? (
        <div className="flex justify-end px-3 py-2">
          <PageNav page={clampedPage} pageCount={pageCount} onPageChange={setPage} />
        </div>
      ) : null}
    </div>
  );
}
