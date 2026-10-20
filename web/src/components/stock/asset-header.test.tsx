// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AssetHeader } from "@/components/stock/asset-header";
import { useLiveQuote } from "@/hooks/use-live-quote";
import { usePriceHistory } from "@/hooks/use-stock-data";
import { getEtfFundInfo, getEtfThemeTags } from "@/lib/api/etf-detail";
import { getEtfList, getSymbolInfo } from "@/lib/api/market";
import { getLivePriceQuote, getStockScore } from "@/lib/api/stock-detail";
import { renderWithIntl } from "@/test/render";
import type { SymbolType } from "@/types/market/shared";
import type { Market } from "@/types/settings";

vi.mock("@/hooks/use-live-quote", () => ({ useLiveQuote: vi.fn() }));
vi.mock("@/hooks/use-stock-data", () => ({ usePriceHistory: vi.fn() }));
vi.mock("@/lib/api/market", () => ({ getSymbolInfo: vi.fn(), getEtfList: vi.fn() }));
vi.mock("@/lib/api/etf-detail", () => ({ getEtfFundInfo: vi.fn(), getEtfThemeTags: vi.fn() }));
vi.mock("@/lib/api/stock-detail", () => ({ getStockScore: vi.fn(), getLivePriceQuote: vi.fn() }));
vi.mock("@/components/stock/watchlist-star-button", () => ({
  WatchlistStarButton: (p: { assetClass: string }) => <i data-star={p.assetClass} />,
}));
vi.mock("@/components/stock/symbol-alert-button", () => ({
  SymbolAlertButton: (p: { isEtf: boolean }) => <i data-alert={String(p.isEtf)} />,
}));
vi.mock("@/components/stock/tw-stock-signal-badges", () => ({
  TwStockSignalBadges: () => <i data-signals="" />,
}));

type Bar = { trade_date: string; open: number; high: number; low: number; volume: number } & {
  close: number;
};

const bar = (trade_date: string, close: number): Bar => ({
  trade_date,
  open: close - 1,
  high: close + 2,
  low: close - 3,
  volume: 123456,
  close,
});

type Setup = {
  readonly bars?: Bar[];
  readonly historyError?: boolean;
  readonly tick?: Record<string, number | null> | null;
  readonly marketOpen?: boolean;
  readonly snapshotTick?: Record<string, number | null> | null;
  readonly score?: unknown;
  readonly etfList?: unknown;
  readonly tags?: string[];
  readonly trackedIndex?: string | null;
};

const SCORE = { found: true, result: { band: "偏多", composite: 71.6 } };

function setup(s: Setup): void {
  vi.mocked(usePriceHistory).mockReturnValue({
    data: { rows: s.bars ?? [] },
    isError: s.historyError ?? false,
  } as never);
  vi.mocked(useLiveQuote).mockReturnValue({
    tick: s.tick ?? null,
    marketOpen: s.marketOpen ?? false,
    asOf: null,
  } as never);
  vi.mocked(getSymbolInfo).mockResolvedValue({
    rows: [{ name: "測試公司", industry_category: "半導體業" }],
  } as never);
  vi.mocked(getEtfFundInfo).mockResolvedValue({
    info: s.trackedIndex === undefined ? null : { tracked_index: s.trackedIndex },
  } as never);
  vi.mocked(getEtfList).mockResolvedValue(
    (s.etfList ?? { data_available: false, rows: [] }) as never,
  );
  vi.mocked(getEtfThemeTags).mockResolvedValue({ tags: s.tags ?? [] } as never);
  vi.mocked(getStockScore).mockResolvedValue((s.score ?? { found: false }) as never);
  vi.mocked(getLivePriceQuote).mockResolvedValue({ tick: s.snapshotTick ?? null } as never);
}

async function html(
  s: Setup,
  props: { market?: Market; symbolType?: SymbolType; symbol?: string } = {},
): Promise<string> {
  setup(s);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = renderWithIntl(
    <QueryClientProvider client={client}>
      <AssetHeader
        symbol={props.symbol ?? "1001"}
        market={props.market ?? "TW"}
        symbolType={props.symbolType ?? "stock"}
      />
    </QueryClientProvider>,
  );
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  // The sparkle icon animates by wall-clock time; pin its transform so the output is stable.
  return view.container.innerHTML.replace(/style="transform:[^"]*"/g, "");
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("AssetHeader characterization", () => {
  it("TW stock: EOD price up, sector chip, band pill, eod-only status", async () => {
    const out = await html({
      bars: [bar("2026-01-02", 110), bar("2026-01-01", 100)],
      score: SCORE,
    });
    expect(out).toContain("半導體業");
    expect(out).toMatchSnapshot();
  });

  it("TW stock: EOD price down with a bearish band", async () => {
    const out = await html({
      bars: [bar("2026-01-01", 100), bar("2026-01-02", 90)],
      score: { found: true, result: { band: "偏空", composite: 30 } },
    });
    expect(out).toMatchSnapshot();
  });

  it("neutral band without a composite shows only the band", async () => {
    expect(
      await html({
        bars: [bar("2026-01-01", 100)],
        score: { found: true, result: { band: "中性" } },
      }),
    ).toMatchSnapshot();
  });

  it("score not found hides the band pill", async () => {
    expect(await html({ bars: [bar("2026-01-01", 100)], score: { found: false } })).not.toContain(
      "stock-score-gauge",
    );
  });

  it("single EOD bar has a price but no change", async () => {
    expect(await html({ bars: [bar("2026-01-01", 100)] })).toMatchSnapshot();
  });

  it("prior close of zero gives no change", async () => {
    expect(await html({ bars: [bar("2026-01-01", 0), bar("2026-01-02", 100)] })).toMatchSnapshot();
  });

  it("no bars at all", async () => {
    expect(await html({})).toMatchSnapshot();
  });

  it("history error shows the delayed status with the warn colour", async () => {
    const out = await html({ historyError: true });
    expect(out).toMatchSnapshot();
  });

  it("US live tick while the market is open", async () => {
    const out = await html(
      {
        tick: {
          price: 200.5,
          change_percent: -0.196,
          open_price: 199,
          day_high: 201,
          day_low: 198,
          day_volume: 5000,
        },
        marketOpen: true,
        bars: [bar("2026-01-01", 100)],
      },
      { market: "US", symbol: "AAPL" },
    );
    expect(out).toMatchSnapshot();
  });

  it("US snapshot tick with market closed shows the EOD-price status", async () => {
    const out = await html(
      {
        snapshotTick: { price: 50, change_percent: 1.2 },
        marketOpen: false,
      },
      { market: "US", symbol: "AAPL" },
    );
    expect(out).toMatchSnapshot();
  });

  it("US with nothing loaded and a history error still reads market-closed", async () => {
    expect(await html({ historyError: true }, { market: "US", symbol: "AAPL" })).toMatchSnapshot();
  });

  it("a live tick with null OHLC fields falls back to the tick values it has", async () => {
    const out = await html(
      { tick: { price: 10, change_percent: 0 }, marketOpen: true },
      { market: "US", symbol: "AAPL" },
    );
    expect(out).toMatchSnapshot();
  });

  it("ETF: tracked index, structure badges, frequency and theme tags", async () => {
    const out = await html(
      {
        bars: [bar("2026-01-01", 20), bar("2026-01-02", 21)],
        trackedIndex: "臺灣指數",
        etfList: {
          data_available: true,
          rows: [
            {
              symbol: "0050",
              is_active_etf: true,
              symbol_type: "etf_leveraged",
              distribution_frequency: "季配",
            },
          ],
        },
        tags: ["高股息", "ESG"],
      },
      { symbolType: "etf", symbol: "0050" },
    );
    expect(out).toMatchSnapshot();
  });

  it("ETF: no tracked index, symbol missing from the list, unavailable list", async () => {
    const missing = await html(
      {
        etfList: {
          data_available: true,
          rows: [{ symbol: "9999", is_active_etf: false, symbol_type: "etf" }],
        },
      },
      { symbolType: "etf", symbol: "0050" },
    );
    cleanup();
    const unavailable = await html({ trackedIndex: null }, { symbolType: "etf", symbol: "0050" });
    expect(missing).toMatchSnapshot();
    expect(unavailable).toMatchSnapshot();
  });
});
