// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MarginShortBody } from "@/components/stock/chip/margin-short";
import type { MarginSubTab } from "@/components/stock/chip/shared";
import {
  getSecuritiesLending,
  getStockDayTrading,
  getStockMarginShort,
} from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";

vi.mock("recharts", async (importOriginal) =>
  (await import("@/test/fixed-size-recharts")).withFixedSizeContainer(
    await importOriginal<typeof import("recharts")>(),
  ),
);
vi.mock("@/lib/api/stock-detail", () => ({
  getStockMarginShort: vi.fn(),
  getStockDayTrading: vi.fn(),
  getSecuritiesLending: vi.fn(),
}));
vi.mock("@/hooks/use-stock-data", () => ({
  usePriceHistory: () => ({
    data: {
      rows: [
        { trade_date: "2026-01-02", close: 100 },
        { trade_date: "2026-01-03", close: 101.5 },
      ],
    },
  }),
}));

const copy = messages.stock.chipAnalysis;

function margin(date: string, over: Record<string, number> = {}): Record<string, unknown> {
  return {
    symbol: "2330",
    market: "TW",
    trade_date: date,
    margin_purchase_buy: 0,
    margin_purchase_cash_repayment: 12,
    margin_purchase_limit: 1000,
    margin_purchase_sell: 0,
    margin_purchase_today_balance: 500,
    margin_purchase_yesterday_balance: 450,
    offset_loan_and_short: 0,
    short_sale_buy: 0,
    short_sale_cash_repayment: 3,
    short_sale_limit: 200,
    short_sale_sell: 0,
    short_sale_today_balance: 50,
    short_sale_yesterday_balance: 60,
    ...over,
  };
}

function dayTrading(date: string, volume: number): Record<string, unknown> {
  return {
    symbol: "2330",
    market: "TW",
    trade_date: date,
    buy_after_sale: "Y",
    volume,
    buy_amount: 1_234_567,
    sell_amount: 2_345_678,
  };
}

function lending(date: string, volume: number, feeRate: number): Record<string, unknown> {
  return {
    symbol: "2330",
    market: "TW",
    trade_date: date,
    transaction_type: "競價",
    volume,
    fee_rate: feeRate,
    close: 100,
  };
}

function envelope(rows: unknown[]): never {
  return { source_id: "x", currentness_caveat: "", health_status: "ok", rows } as never;
}

function mount(view: MarginSubTab): HTMLElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithIntl(
    <QueryClientProvider client={client}>
      <MarginShortBody symbol="2330" market="TW" view={view} />
    </QueryClientProvider>,
  ).container;
}

async function ready(view: MarginSubTab): Promise<HTMLElement> {
  const c = mount(view);
  await screen.findByRole("table");
  return c;
}

function headers(): string[] {
  return Array.from(document.querySelectorAll("thead th")).map((th) => th.textContent ?? "");
}

function bodyRows(root: ParentNode = document): string[][] {
  return Array.from(root.querySelectorAll("tbody tr")).map((tr) =>
    Array.from(tr.querySelectorAll("td")).map((td) => td.textContent ?? ""),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("MarginShortBody margin and short", () => {
  const rows = [
    margin("2026-01-03", {
      margin_purchase_today_balance: 600,
      margin_purchase_yesterday_balance: 500,
      short_sale_today_balance: 40,
      short_sale_yesterday_balance: 50,
    }),
    margin("2026-01-02"),
  ];

  it("margin: balance, change, usage and repayment, newest first, only the margin query runs", async () => {
    vi.mocked(getStockMarginShort).mockResolvedValue(envelope(rows));
    const c = await ready("margin");
    expect(headers()).toEqual([
      copy.flowTable.date,
      copy.marginBalance,
      copy.marginChange,
      copy.marginUsage,
      copy.marginCashRepayment,
    ]);
    const body = bodyRows();
    expect(body.map((r) => r[0])).toEqual(["2026-01-03", "2026-01-02"]);
    expect(body[0]?.[1]).toContain("600");
    expect(body[0]?.[3]).toBe("60.00%");
    expect(getStockDayTrading).not.toHaveBeenCalled();
    expect(getSecuritiesLending).not.toHaveBeenCalled();
    expect(c.querySelector("table")?.outerHTML).toMatchSnapshot();
  });

  it("short: uses the short side's balance, change, usage and repayment", async () => {
    vi.mocked(getStockMarginShort).mockResolvedValue(envelope(rows));
    const c = await ready("short");
    expect(headers()).toEqual([
      copy.flowTable.date,
      copy.shortBalance,
      copy.shortChange,
      copy.shortUsage,
      copy.shortCashRepayment,
    ]);
    const body = bodyRows();
    expect(body[0]?.[1]).toContain("40");
    expect(body[0]?.[3]).toBe("20.00%");
    expect(c.querySelector("table")?.outerHTML).toMatchSnapshot();
  });

  it("shows a dash for usage when the limit is zero", async () => {
    vi.mocked(getStockMarginShort).mockResolvedValue(
      envelope([margin("2026-01-02", { margin_purchase_limit: 0 })]),
    );
    await ready("margin");
    expect(bodyRows()[0]?.[3]).toBe("—");
  });

  it("sorts rows by date even when the API sends them newest first", async () => {
    vi.mocked(getStockMarginShort).mockResolvedValue(
      envelope([margin("2026-01-03"), margin("2026-01-01"), margin("2026-01-02")]),
    );
    await ready("margin");
    expect(bodyRows().map((r) => r[0])).toEqual(["2026-01-03", "2026-01-02", "2026-01-01"]);
  });

  it("collapses to six rows and expands the rest in a modal", async () => {
    const many = Array.from({ length: 9 }, (_, i) =>
      margin(`2026-01-${String(i + 1).padStart(2, "0")}`),
    );
    vi.mocked(getStockMarginShort).mockResolvedValue(envelope(many));
    await ready("margin");
    expect(bodyRows()).toHaveLength(6);
    fireEvent.click(screen.getByRole("button", { name: copy.expandTo.replace("{count}", "9") }));
    const dialog = await screen.findByRole("dialog");
    expect(bodyRows(dialog)).toHaveLength(9);
  });

  it("offers no expand button for six rows or fewer", async () => {
    vi.mocked(getStockMarginShort).mockResolvedValue(envelope(rows));
    await ready("margin");
    expect(screen.queryByRole("button", { name: /展開|expand/i })).toBeNull();
  });

  it("shows a skeleton while loading, an error with retry, and the empty line", async () => {
    vi.mocked(getStockMarginShort).mockReturnValue(new Promise(() => undefined));
    const loading = mount("margin");
    expect(loading.querySelector("table")).toBeNull();
    expect(loading.innerHTML).toMatchSnapshot();
    cleanup();
    vi.mocked(getStockMarginShort).mockRejectedValue(new Error("x"));
    mount("margin");
    expect(await screen.findByRole("alert")).toBeTruthy();
    cleanup();
    vi.mocked(getStockMarginShort).mockResolvedValue(envelope([]));
    mount("margin");
    expect(await screen.findByText(copy.empty)).toBeTruthy();
  });
});

describe("MarginShortBody ratio", () => {
  it("computes 券資比 from the two balances, 0 when there is no margin balance", async () => {
    vi.mocked(getStockMarginShort).mockResolvedValue(
      envelope([
        margin("2026-01-03", {
          margin_purchase_today_balance: 0,
          short_sale_today_balance: 30,
        }),
        margin("2026-01-02", {
          margin_purchase_today_balance: 400,
          short_sale_today_balance: 100,
        }),
      ]),
    );
    const c = await ready("ratio");
    expect(headers()).toEqual([
      copy.flowTable.date,
      copy.shortToMarginRatio,
      copy.marginBalance,
      copy.shortBalance,
    ]);
    const body = bodyRows();
    expect(body[0]?.[1]).toBe("0.00%");
    expect(body[1]?.[1]).toBe("25.00%");
    expect(c.querySelector("table")?.outerHTML).toMatchSnapshot();
  });

  it("covers the loading, error and empty states of the shared margin query", async () => {
    vi.mocked(getStockMarginShort).mockRejectedValue(new Error("x"));
    mount("ratio");
    expect(await screen.findByRole("alert")).toBeTruthy();
    cleanup();
    vi.mocked(getStockMarginShort).mockResolvedValue(envelope([]));
    mount("ratio");
    expect(await screen.findByText(copy.empty)).toBeTruthy();
  });
});

describe("MarginShortBody day trading", () => {
  it("converts shares to lots and shows the money amounts as they are", async () => {
    vi.mocked(getStockDayTrading).mockResolvedValue(
      envelope([dayTrading("2026-01-03", 2_500_000), dayTrading("2026-01-02", 1_000_000)]),
    );
    const c = await ready("dayTrading");
    expect(headers()).toEqual([
      copy.flowTable.date,
      copy.dayTradingVolume,
      copy.dayTradingBuyAmount,
      copy.dayTradingSellAmount,
    ]);
    const body = bodyRows();
    expect(body[0]?.[1]).toContain("2,500");
    expect(body[0]?.[2]).toBe("1,234,567");
    expect(getStockMarginShort).not.toHaveBeenCalled();
    expect(getSecuritiesLending).not.toHaveBeenCalled();
    expect(c.querySelector("table")?.outerHTML).toMatchSnapshot();
  });

  it("covers loading, error and empty", async () => {
    vi.mocked(getStockDayTrading).mockReturnValue(new Promise(() => undefined));
    const loading = mount("dayTrading");
    expect(loading.querySelector("table")).toBeNull();
    cleanup();
    vi.mocked(getStockDayTrading).mockRejectedValue(new Error("x"));
    mount("dayTrading");
    expect(await screen.findByRole("alert")).toBeTruthy();
    cleanup();
    vi.mocked(getStockDayTrading).mockResolvedValue(envelope([]));
    mount("dayTrading");
    expect(await screen.findByText(copy.empty)).toBeTruthy();
  });
});

describe("MarginShortBody lending", () => {
  it("sums same-day rows, averages their fee rate and accumulates the balance", async () => {
    vi.mocked(getSecuritiesLending).mockResolvedValue(
      envelope([
        lending("2026-01-03", 1_000_000, 0.5),
        lending("2026-01-02", 2_000_000, 1),
        lending("2026-01-02", 1_000_000, 2),
      ]),
    );
    const c = await ready("lending");
    expect(headers()).toEqual([
      copy.flowTable.date,
      copy.lendingSellVolume,
      copy.lendingDailyVolume,
      copy.lendingFeeRate,
    ]);
    const body = bodyRows();
    expect(body).toHaveLength(2);
    // 2026-01-02: 3,000 lots that day and a 1.50% average fee; 01-03 adds 1,000 → 4,000 balance.
    expect(body[1]?.[2]).toContain("3,000");
    expect(body[1]?.[3]).toBe("1.50%");
    expect(body[0]?.[1]).toContain("4,000");
    expect(body[0]?.[2]).toContain("1,000");
    expect(getStockMarginShort).not.toHaveBeenCalled();
    expect(getStockDayTrading).not.toHaveBeenCalled();
    expect(c.querySelector("table")?.outerHTML).toMatchSnapshot();
  });

  it("covers loading, error and empty", async () => {
    vi.mocked(getSecuritiesLending).mockReturnValue(new Promise(() => undefined));
    const loading = mount("lending");
    expect(loading.querySelector("table")).toBeNull();
    cleanup();
    vi.mocked(getSecuritiesLending).mockRejectedValue(new Error("x"));
    mount("lending");
    expect(await screen.findByRole("alert")).toBeTruthy();
    cleanup();
    vi.mocked(getSecuritiesLending).mockResolvedValue(envelope([]));
    mount("lending");
    expect(await screen.findByText(copy.empty)).toBeTruthy();
  });
});

describe("MarginShortBody chart", () => {
  it("draws the bars and the 股價 line from the price history", async () => {
    vi.mocked(getStockMarginShort).mockResolvedValue(
      envelope([margin("2026-01-02"), margin("2026-01-03")]),
    );
    const c = await ready("margin");
    expect(c.querySelectorAll(".recharts-bar-rectangle").length).toBeGreaterThan(0);
    expect(c.querySelector(".recharts-line")).not.toBeNull();
  });
});
