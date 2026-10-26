// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RealtimeTab } from "@/components/stock/realtime-panel";
import {
  getIntradayKbar,
  getLiveQuote,
  getPriceLimit,
  getPriceTick,
  getStockDayTrading,
} from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";

vi.mock("lightweight-charts", () => ({
  BaselineSeries: "baseline",
  CandlestickSeries: "candlestick",
  HistogramSeries: "histogram",
  createChart: vi.fn(),
}));
vi.mock("@/i18n/navigation", async () => ({
  Link: (await import("@/test/link-stub")).LinkStub,
}));
vi.mock("@/lib/api/stock-detail", () => ({
  getIntradayKbar: vi.fn(),
  getLiveQuote: vi.fn(),
  getPriceLimit: vi.fn(),
  getPriceTick: vi.fn(),
  getStockDayTrading: vi.fn(),
}));

const copy = messages.stock.realtime;

const MASKED = { locked: true, required_tier: "pro", preview: "預覽", tool_name: "x" };

function envelope(extra: Record<string, unknown>): never {
  return { source_id: "x", currentness_caveat: "", health_status: "ok", ...extra } as never;
}

function limit(over: Record<string, number> = {}): never {
  return envelope({
    rows: [
      {
        symbol: "2330",
        market: "TW",
        trade_date: "2026-10-16",
        reference_price: 100,
        limit_up: 110,
        limit_down: 90,
        ...over,
      },
    ],
  });
}

function quote(over: Record<string, unknown> | null = {}): never {
  return envelope({
    snapshot:
      over === null
        ? null
        : {
            symbol: "2330",
            market: "TW",
            last_price: 103,
            open: 100,
            high: 105,
            low: 98,
            volume: 12345,
            ...over,
          },
  });
}

function tick(type: string, price: number, volume: number): Record<string, unknown> {
  return {
    symbol: "2330",
    market: "TW",
    trade_date: "2026-10-16",
    time: "09:00:00",
    deal_price: price,
    volume,
    tick_type: type,
  };
}

function ticks(rows: Record<string, unknown>[]): never {
  return envelope({ rows });
}

function dayTradingRows(rows: Record<string, unknown>[]): never {
  return envelope({ rows });
}

const DAY = {
  symbol: "2330",
  market: "TW",
  trade_date: "2026-10-16",
  buy_after_sale: "Y",
  volume: 1_000_000,
  buy_amount: 1_000_000,
  sell_amount: 2_000_000,
};

function mount(): HTMLElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithIntl(
    <QueryClientProvider client={client}>
      <RealtimeTab symbol="2330" market="TW" />
    </QueryClientProvider>,
  ).container;
}

async function settle(): Promise<void> {
  for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0));
}

function detailSection(): HTMLElement | null {
  return screen.queryByRole("heading", { name: copy.detailTitle })?.closest("section") ?? null;
}

/** label → value text of the 詳細 grid. */
function detailStats(): Record<string, string> {
  const section = detailSection();
  const out: Record<string, string> = {};
  for (const label of section?.querySelectorAll("p.text-\\[11px\\]") ?? []) {
    const value = label.nextElementSibling?.textContent ?? "";
    out[label.textContent ?? ""] = value;
  }
  return out;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getStockDayTrading).mockResolvedValue(dayTradingRows([DAY]));
  vi.mocked(getIntradayKbar).mockResolvedValue(MASKED as never);
  vi.mocked(getLiveQuote).mockResolvedValue(quote());
  vi.mocked(getPriceLimit).mockResolvedValue(limit());
  vi.mocked(getPriceTick).mockResolvedValue(
    ticks([tick("1", 101, 10), tick("2", 99, 30), tick("0", 100, 60)]),
  );
});
afterEach(cleanup);

describe("RealtimeTab 詳細 stats", () => {
  it("shows the limit pills, the live figures and the tick-derived figures", async () => {
    const c = mount();
    await settle();
    const s = detailStats();
    expect(s[copy.limitDown]).toBe("90.00");
    expect(s[copy.limitUp]).toBe("110.00");
    expect(s[copy.referencePrice]).toBe("100.00");
    expect(s[copy.lastPrice]).toBe("103.00");
    expect(s[copy.amplitude]).toBe("7.00%");
    expect(s[copy.low]).toBe("98.00");
    expect(s[copy.high]).toBe("105.00");
    expect(s[copy.totalVolume]).toBe("12,345");
    expect(s[copy.insideVolume]).toBe("30");
    expect(s[copy.outsideVolume]).toBe("10");
    expect(s[copy.lastTradeSize]).toBe("60");
    // (101*10 + 99*30 + 100*60) / 100
    expect(s[copy.averagePrice]).toBe("99.80");
    expect(detailSection()?.textContent).toContain("+3.00".replace("+", ""));
    expect(detailSection()?.querySelector("[role=group]")?.getAttribute("aria-label")).toContain(
      "3.00",
    );
    expect(detailSection()?.innerHTML).toMatchSnapshot();
    expect(c.querySelector("h2")).not.toBeNull();
  });

  it("marks a price below the reference as a fall", async () => {
    vi.mocked(getLiveQuote).mockResolvedValue(quote({ last_price: 96 }));
    mount();
    await settle();
    const group = detailSection()?.querySelector("[role=group]");
    expect(group?.getAttribute("aria-label")).toContain(messages.common.direction.down);
    expect(group?.textContent).toContain("▼");
    expect(group?.textContent).toContain("4.00");
  });

  it("marks a price above the reference as a rise", async () => {
    mount();
    await settle();
    const group = detailSection()?.querySelector("[role=group]");
    expect(group?.getAttribute("aria-label")).toContain(messages.common.direction.up);
    expect(group?.textContent).toContain("▲");
  });

  it("shows dashes for the live figures when there is no live snapshot", async () => {
    vi.mocked(getLiveQuote).mockResolvedValue(quote(null));
    mount();
    await settle();
    const s = detailStats();
    for (const label of [
      copy.lastPrice,
      copy.change,
      copy.amplitude,
      copy.low,
      copy.high,
      copy.totalVolume,
    ]) {
      expect(s[label]).toBe("—");
    }
    expect(s[copy.limitUp]).toBe("110.00");
    expect(s[copy.insideVolume]).toBe("30");
  });

  it("has no percent change or amplitude when the reference price is zero", async () => {
    vi.mocked(getPriceLimit).mockResolvedValue(limit({ reference_price: 0 }));
    mount();
    await settle();
    const s = detailStats();
    expect(s[copy.change]).toBe("—");
    expect(s[copy.amplitude]).toBe("—");
    expect(s[copy.lastPrice]).toBe("103.00");
  });

  it("shows dashes for the tick-derived figures when ticks are masked or empty", async () => {
    vi.mocked(getPriceTick).mockResolvedValue(MASKED as never);
    mount();
    await settle();
    let s = detailStats();
    for (const label of [
      copy.insideVolume,
      copy.outsideVolume,
      copy.lastTradeSize,
      copy.averagePrice,
    ]) {
      expect(s[label]).toBe("—");
    }
    cleanup();
    vi.mocked(getPriceTick).mockResolvedValue(ticks([]));
    mount();
    await settle();
    s = detailStats();
    expect(s[copy.insideVolume]).toBe("—");
    expect(s[copy.lastTradeSize]).toBe("—");
  });

  it("shows 0 volumes but no average when every tick has zero volume", async () => {
    vi.mocked(getPriceTick).mockResolvedValue(ticks([tick("1", 101, 0), tick("2", 99, 0)]));
    mount();
    await settle();
    const s = detailStats();
    expect(s[copy.insideVolume]).toBe("0");
    expect(s[copy.outsideVolume]).toBe("0");
    expect(s[copy.lastTradeSize]).toBe("0");
    expect(s[copy.averagePrice]).toBe("—");
  });

  it("renders nothing for the section when there is no limit row, or while it loads", async () => {
    vi.mocked(getPriceLimit).mockResolvedValue(envelope({ rows: [] }));
    mount();
    await settle();
    expect(detailSection()).toBeNull();
    cleanup();
    vi.mocked(getPriceLimit).mockReturnValue(new Promise(() => undefined));
    mount();
    await settle();
    expect(detailSection()).toBeNull();
  });

  it("stays a skeleton until a trading day is known", async () => {
    vi.mocked(getStockDayTrading).mockResolvedValue(dayTradingRows([]));
    mount();
    await settle();
    expect(detailSection()).toBeNull();
    expect(getPriceLimit).not.toHaveBeenCalled();
    expect(getPriceTick).not.toHaveBeenCalled();
  });

  it("asks for the latest trading day's limit and ticks only", async () => {
    vi.mocked(getStockDayTrading).mockResolvedValue(
      dayTradingRows([{ ...DAY, trade_date: "2026-10-14" }, DAY]),
    );
    mount();
    await settle();
    expect(getPriceLimit).toHaveBeenCalledWith("2330", "TW", {
      startDate: "2026-10-16",
      endDate: "2026-10-16",
    });
    expect(getPriceTick).toHaveBeenCalledWith("2330", "TW", {
      startDate: "2026-10-16",
      endDate: "2026-10-16",
    });
    expect(detailSection()?.textContent).toContain(
      copy.dayTradingAsOf.replace("{date}", "2026-10-16"),
    );
  });
});
