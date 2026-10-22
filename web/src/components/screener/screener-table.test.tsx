// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ScreenerColumn } from "@/components/screener/screener-market-config";
import { ScreenerTable } from "@/components/screener/screener-table";
import type { SymbolQuote } from "@/hooks/use-symbol-quotes";
import { messages, renderWithIntl } from "@/test/render";
import type { FactorCatalog, ScreenerMarket, ScreenerSurvivor } from "@/types/screener";
import type { WatchlistGroupView } from "@/types/watchlist";

// A tiny external store stands in for the watchlist query cache so starring re-renders the table
// the way the real optimistic update does.
const store = vi.hoisted(() => {
  let starred: readonly string[] = [];
  const listeners = new Set<() => void>();
  return {
    get: (): readonly string[] => starred,
    set: (next: readonly string[]): void => {
      starred = next;
      for (const listener of listeners) listener();
    },
    subscribe: (listener: () => void): (() => void) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
  };
});

function groupView(symbols: readonly string[]): WatchlistGroupView[] {
  return [
    {
      group: {
        id: "g1",
        user_id: "u",
        name: "預設",
        market: "TW",
        asset_class: "stock",
        sort_order: "a",
        is_default: true,
        color: null,
        version: 1,
        created_at: "",
        updated_at: "",
      },
      items: symbols.map((symbol) => ({
        id: symbol,
        watchlist_id: "g1",
        user_id: "u",
        symbol,
        market: "TW",
        asset_class: "stock",
        note: null,
        sort_order: "a",
        added_at: "",
      })),
    },
  ];
}

vi.mock("@/hooks/use-watchlists", async () => {
  const actual =
    await vi.importActual<typeof import("@/hooks/use-watchlists")>("@/hooks/use-watchlists");
  return {
    ...actual,
    useWatchlists: () => ({
      data: groupView(useSyncExternalStore(store.subscribe, store.get, store.get)),
    }),
    useAddWatchlistItem: () => ({
      mutate: ({ symbol }: { symbol: string }) => store.set([...store.get(), symbol]),
    }),
    useUnstarSymbolEverywhere: () => ({
      mutate: ({ symbol }: { symbol: string }) =>
        store.set(store.get().filter((s) => s !== symbol)),
    }),
  };
});

const quotes: SymbolQuote[] = [
  {
    code: "2330",
    name: "台積電",
    industryCategory: "半導體業",
    price: 1000,
    changePct: 1.5,
    sparkline: [],
    volume: 12_345,
  },
  {
    code: "2317",
    name: "鴻海",
    industryCategory: "其他電子業",
    price: 150,
    changePct: -2.25,
    sparkline: [],
    volume: null,
  },
];
vi.mock("@/hooks/use-symbol-quotes", () => ({ useSymbolQuotes: () => ({ quotes }) }));
vi.mock("@/i18n/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const copy = messages.screener.table;
const columns: ScreenerColumn[] = [
  { key: "chip", labelZh: "籌碼", labelEn: "Chip", sponsor: true },
];
const survivors: ScreenerSurvivor[] = [
  { symbol: "2330", matched_conditions: ["rsi_oversold", "unknown_key"], display: {} },
  { symbol: "2317", matched_conditions: [], display: {} },
];
// Only the label lookup is exercised, so the catalog is cut down to the fields the table reads.
const catalog = {
  fields: [{ key: "rsi_oversold", label_zh: "RSI 超賣" }],
  signals: [],
} as unknown as FactorCatalog;

function table(market: ScreenerMarket = "TW", rows: ScreenerSurvivor[] = survivors) {
  return <ScreenerTable survivors={rows} columns={columns} market={market} catalog={catalog} />;
}

function rowOf(symbol: string): HTMLElement {
  const row = screen.getByText(symbol).closest("tr");
  if (!row) throw new Error(`row ${symbol} missing`);
  return row;
}

beforeEach(() => store.set([]));
afterEach(cleanup);

describe("ScreenerTable cells", () => {
  it("renders symbol, name, sector, price, volume, sponsor lock and matched labels", () => {
    renderWithIntl(table());
    const cells = within(rowOf("2330"));
    expect(cells.getByText("台積電")).toBeTruthy();
    expect(cells.getByText("半導體業")).toBeTruthy();
    expect(cells.getByText("1,000")).toBeTruthy();
    expect(cells.getByText("12,345")).toBeTruthy();
    expect(cells.getByText(copy.sponsorLocked)).toBeTruthy();
    // a known key is labelled from the catalog, an unknown one falls back to the raw key
    expect(cells.getByText("RSI 超賣")).toBeTruthy();
    expect(cells.getByText("unknown_key")).toBeTruthy();
  });

  it("renders signed change with a direction aria-label and a dash for a missing volume", () => {
    renderWithIntl(table());
    expect(screen.getByLabelText("上漲 1.50%")).toBeTruthy();
    expect(screen.getByLabelText("下跌 2.25%")).toBeTruthy();
    expect(within(rowOf("2317")).getAllByText("—")).toHaveLength(1);
  });

  it("renders the US-only columns from survivor.display", () => {
    renderWithIntl(
      table("US", [
        {
          symbol: "AAPL",
          matched_conditions: [],
          display: {
            regularMarketPrice: 200,
            regularMarketChangePercent: 0.5,
            regularMarketVolume: 1000,
            marketCap: 3_000_000_000_000,
            "peratio.lasttwelvemonths": 31.24,
            pctheldinst: 61.5,
            "short_percentage_of_float.value": 0.8,
          },
        },
      ]),
    );
    expect(screen.getByText("3000.0B")).toBeTruthy();
    expect(screen.getByText("31.2x")).toBeTruthy();
    expect(screen.getByText("61.5%")).toBeTruthy();
    expect(screen.getByText("0.8%")).toBeTruthy();
  });
});

describe("ScreenerTable star toggle", () => {
  it("toggles aria-pressed", () => {
    renderWithIntl(table());
    const first = screen.getAllByRole("button", { name: copy.star })[0];
    expect(first?.getAttribute("aria-pressed")).toBe("false");
    if (first) fireEvent.click(first);
    expect(
      screen.getAllByRole("button", { name: copy.star })[0]?.getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("keeps the same star button element, and its focus, after toggling", () => {
    renderWithIntl(table());
    const first = screen.getAllByRole("button", { name: copy.star })[0];
    if (!first) throw new Error("star button missing");
    first.focus();
    fireEvent.click(first);
    expect(screen.getAllByRole("button", { name: copy.star })[0]).toBe(first);
    expect(document.activeElement).toBe(first);
  });
});
