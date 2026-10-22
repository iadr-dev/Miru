// @vitest-environment jsdom
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StockComparePanel } from "@/components/stock/compare/compare-panel";
import { usePriceHistories } from "@/hooks/use-stock-data";
import { useSymbolSearch } from "@/hooks/use-symbol-search";
import { messages, renderWithIntl } from "@/test/render";
import type { PriceHistoryResponse } from "@/types/market/quotes";

const setParams = vi.fn();
let search = "cmp=2330,2454";

vi.mock("@/hooks/use-url-params", () => ({
  useUrlParams: () => ({ searchParams: new URLSearchParams(search), setParams }),
}));
vi.mock("@/hooks/use-compare-peers", () => ({ useComparePeers: () => [] }));
vi.mock("@/hooks/use-symbol-quotes", () => ({
  useSymbolQuotes: (symbols: readonly string[]) => ({
    quotes: symbols.map((code) => ({
      code,
      name: NAMES[code] ?? null,
      industryCategory: null,
      price: null,
      changePct: null,
      sparkline: [],
      volume: null,
    })),
    isLoading: false,
    isError: false,
  }),
}));
vi.mock("@/hooks/use-stock-data", () => ({ usePriceHistories: vi.fn() }));
vi.mock("@/hooks/use-symbol-search", () => ({ useSymbolSearch: vi.fn() }));

const NAMES: Record<string, string> = { "2330": "台積電", "2454": "聯發科", "2317": "鴻海" };
const copy = messages.stock.technical.compare;

function history(symbol: string, closes: readonly [string, number][]): PriceHistoryResponse {
  return {
    market: "TW",
    start_date: closes[0]?.[0] ?? null,
    end_date: closes.at(-1)?.[0] ?? null,
    data_available: true,
    rows: closes.map(([trade_date, close]) => ({
      symbol,
      market: "TW",
      trade_date,
      open: close,
      high: close,
      low: close,
      close,
      volume: 1000,
    })),
  };
}

function mockHistories(
  histories: (PriceHistoryResponse | undefined)[],
  extra: { isLoading?: boolean; isError?: boolean; retry?: () => void } = {},
): void {
  vi.mocked(usePriceHistories).mockReturnValue({
    histories,
    isLoading: extra.isLoading ?? false,
    isError: extra.isError ?? false,
    error: extra.isError ? new Error("down") : null,
    retry: extra.retry ?? vi.fn(),
  });
}

const TSMC = history("2330", [
  ["2026-03-02", 1000],
  ["2026-03-03", 1050],
  ["2026-03-04", 1100],
]);
const MTK = history("2454", [
  ["2026-03-02", 1000],
  ["2026-03-03", 950],
  ["2026-03-04", 900],
]);

beforeEach(() => {
  search = "cmp=2330,2454";
  setParams.mockReset();
  vi.mocked(useSymbolSearch).mockReturnValue({
    data: [],
    isPending: false,
  } as unknown as ReturnType<typeof useSymbolSearch>);
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: false,
        media: query,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("StockComparePanel", () => {
  it("shows the chips, the signed end-of-range change per stock and the data date, without the caveat chip", () => {
    mockHistories([TSMC, MTK]);
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);

    const chips = screen.getByRole("list", { name: copy.selectedLabel });
    expect(chips.textContent).toContain("台積電");
    expect(chips.textContent).toContain("聯發科");

    // 1100/1000 -> up 10.00%, 900/1000 -> down 10.00%: the direction is said in words, not colour.
    const labels = screen.getAllByRole("img").map((el) => el.getAttribute("aria-label") ?? "");
    expect(
      labels.some(
        (label) => label.includes(messages.common.direction.up) && label.includes("10.00%"),
      ),
    ).toBe(true);
    expect(
      labels.some(
        (label) => label.includes(messages.common.direction.down) && label.includes("10.00%"),
      ),
    ).toBe(true);
    expect(screen.queryByText(/不構成投資建議/)).toBeNull();
    expect(screen.getByText(copy.asOf.replace("{date}", "2026-03-04"))).toBeTruthy();
    expect(screen.getByText(copy.baseNote.replace("{date}", "2026-03-02"))).toBeTruthy();
  });

  it("toggles a line from the legend and reports the state with aria-pressed", () => {
    mockHistories([TSMC, MTK]);
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);

    const legendButton = screen.getByRole("button", { name: /2330.*台積電/, pressed: true });
    expect(legendButton.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(legendButton);
    expect(legendButton.getAttribute("aria-pressed")).toBe("false");
    expect(screen.getByText(copy.legendHidden)).toBeTruthy();
    fireEvent.click(legendButton);
    expect(legendButton.getAttribute("aria-pressed")).toBe("true");
    expect(screen.queryByText(copy.legendHidden)).toBeNull();
  });

  it("says plainly when a stock's history starts later than the range", () => {
    const young = history("2454", [
      ["2026-03-03", 100],
      ["2026-03-04", 110],
    ]);
    mockHistories([TSMC, young]);
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);

    expect(
      screen.getByText(
        copy.limitedNote.replace("{names}", "聯發科").replace("{date}", "2026-03-03"),
      ),
    ).toBeTruthy();
  });

  it("lists a stock with no price data instead of drawing it", () => {
    mockHistories([TSMC, { ...MTK, rows: [], data_available: false }]);
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);

    expect(screen.getByText(copy.missingNote.replace("{names}", "聯發科"))).toBeTruthy();
  });

  it("asks for a second stock when only one is selected", () => {
    search = "cmp=2330";
    mockHistories([TSMC]);
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);

    expect(screen.getByText(copy.needMore)).toBeTruthy();
    expect(screen.queryByText(copy.legendLabel)).toBeNull();
  });

  it("holds a loading skeleton and a retryable error ", () => {
    mockHistories([undefined, undefined], { isLoading: true });
    const view = renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);
    expect(screen.queryByText(copy.legendLabel)).toBeNull();
    expect(screen.queryByText(/不構成投資建議/)).toBeNull();
    view.unmount();

    const retry = vi.fn();
    mockHistories([undefined, undefined], { isError: true, retry });
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);
    fireEvent.click(screen.getByRole("button", { name: messages.market.cards.retry }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it("stops at five stocks: the input is disabled with a calm hint, and removing one writes the URL", () => {
    search = "cmp=2330,2454,2317,2303,2412";
    mockHistories([TSMC, MTK, TSMC, MTK, TSMC]);
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);

    const input = screen.getByRole("combobox", { name: copy.pickerLabel });
    expect((input as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(copy.limitHint.replace("{max}", "5"))).toBeTruthy();

    fireEvent.click(
      screen.getByRole("button", { name: copy.remove.replace("{name}", "2317 鴻海") }),
    );
    expect(setParams).toHaveBeenCalledWith({ sub: "compare", cmp: "2330,2454,2303,2412" });
  });

  it("adds a searched stock to the URL list", async () => {
    mockHistories([TSMC, MTK]);
    vi.mocked(useSymbolSearch).mockReturnValue({
      data: [{ kind: "stock", symbol: "2317", name: "鴻海", market: "TW", secondaryLabel: null }],
      isPending: false,
    } as unknown as ReturnType<typeof useSymbolSearch>);
    renderWithIntl(<StockComparePanel symbol="2330" market="TW" />);

    fireEvent.change(screen.getByRole("combobox", { name: copy.pickerLabel }), {
      target: { value: "鴻" },
    });
    const option = await waitFor(() => screen.getByRole("option", { name: /2317/ }));
    fireEvent.click(option);
    expect(setParams).toHaveBeenCalledWith({ sub: "compare", cmp: "2330,2454,2317" });
  });
});
