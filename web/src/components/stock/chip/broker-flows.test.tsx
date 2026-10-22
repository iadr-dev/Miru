// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ChipFlowsBody } from "@/components/stock/chip/broker-flows";
import { getForeignShareholding, getStockChipFlows } from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";

vi.mock("@/lib/api/stock-detail", () => ({
  getStockChipFlows: vi.fn(),
  getForeignShareholding: vi.fn(),
}));

const copy = messages.stock.chipAnalysis;

type TopRow = {
  rank: number;
  broker_id: string | null;
  broker_name: string | null;
  net: number | null;
  buy: number | null;
  sell: number | null;
  locked: boolean;
};

function top(rank: number, over: Partial<TopRow> = {}): TopRow {
  return {
    rank,
    broker_id: `B${rank}`,
    broker_name: `券商${rank}`,
    net: 100_000 - rank * 1000,
    buy: 150_000,
    sell: 50_000,
    locked: false,
    ...over,
  };
}

function flows(over: Record<string, unknown> = {}): never {
  return {
    source_id: "x",
    currentness_caveat: "",
    health_status: "ok",
    total_volume: 4_000_000,
    main_force_net: 1_000_000,
    concentration_days_pct: 60,
    latest_trade_date: "2026-10-16",
    free_row_count: 0,
    buy_top: [top(1), top(2)],
    sell_top: [top(1, { net: -80_000, broker_name: "賣方一" })],
    top_brokers: [],
    rows: [],
    ...over,
  } as never;
}

function holdings(rows: { trade_date: string; shares: number }[]): never {
  return {
    source_id: "x",
    currentness_caveat: "",
    health_status: "ok",
    rows: rows.map((r) => ({
      symbol: "2330",
      market: "TW",
      trade_date: r.trade_date,
      number_of_shares_issued: r.shares,
    })),
  } as never;
}

function mount(): HTMLElement {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithIntl(
    <QueryClientProvider client={client}>
      <ChipFlowsBody symbol="2330" market="TW" />
    </QueryClientProvider>,
  ).container;
}

async function ready(): Promise<HTMLElement> {
  const c = mount();
  await screen.findAllByText(copy.mainForceNet);
  await new Promise((r) => setTimeout(r, 0));
  return c;
}

function stats(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const label of [
    copy.mainForceNet,
    copy.mainForceConcentration,
    copy.sharesOfCapital,
    copy.concentrationDays,
    copy.brokerTotalVolume,
    copy.turnoverRatio,
  ]) {
    out[label] = screen.getByText(label).parentElement?.textContent?.replace(label, "") ?? "";
  }
  return out;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getForeignShareholding).mockResolvedValue(
    holdings([
      { trade_date: "2026-10-16", shares: 25_000_000_000 },
      { trade_date: "2026-10-01", shares: 1 },
    ]),
  );
});
afterEach(cleanup);

describe("ChipFlowsBody states", () => {
  it("shows a skeleton while loading", () => {
    vi.mocked(getStockChipFlows).mockReturnValue(new Promise(() => undefined));
    const c = mount();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("shows a retryable error", async () => {
    vi.mocked(getStockChipFlows).mockRejectedValue(new Error("x"));
    mount();
    expect(await screen.findByRole("alert")).toBeTruthy();
  });

  it("shows the Sponsor teaser when the data is masked", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue({
      locked: true,
      required_tier: "pro",
      preview: "預覽文字",
      tool_name: "get_chip_flows",
    } as never);
    const c = mount();
    await screen.findByText("預覽文字");
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("says so when there is no buy or sell ranking", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(flows({ buy_top: [], sell_top: [] }));
    mount();
    expect(await screen.findByText(copy.empty)).toBeTruthy();
  });
});

describe("ChipFlowsBody data", () => {
  it("shows the stats, ratios against the latest shares issued, and the buy table", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(flows());
    const c = await ready();
    const s = stats();
    expect(s[copy.mainForceNet]).toContain("1,000");
    expect(s[copy.mainForceConcentration]).toBe("25.00%");
    expect(s[copy.sharesOfCapital]).toBe("0.00%");
    expect(s[copy.concentrationDays]).toBe("60%");
    expect(s[copy.brokerTotalVolume]).toContain("4,000");
    expect(s[copy.turnoverRatio]).toBe("0.02%");
    expect(screen.getByText("2026-10-16", { exact: false })).toBeTruthy();
    expect(screen.getByRole("heading", { name: copy.buyTop15 })).toBeTruthy();
    expect(screen.getByText("券商1")).toBeTruthy();
    expect(c.innerHTML).toMatchSnapshot();
  });

  it("shows dashes when there is no volume, no shares issued, or no concentration figure", async () => {
    vi.mocked(getForeignShareholding).mockResolvedValue(holdings([]));
    vi.mocked(getStockChipFlows).mockResolvedValue(
      flows({ total_volume: 0, concentration_days_pct: null, latest_trade_date: null }),
    );
    await ready();
    const s = stats();
    expect(s[copy.mainForceConcentration]).toBe("—");
    expect(s[copy.sharesOfCapital]).toBe("—");
    expect(s[copy.concentrationDays]).toBe("—");
    expect(s[copy.turnoverRatio]).toBe("—");
    expect(screen.queryByText(copy.asOfDate.replace("{date}", "2026-10-16"))).toBeNull();
  });

  it("treats a zero shares-issued figure as unknown", async () => {
    vi.mocked(getForeignShareholding).mockResolvedValue(
      holdings([{ trade_date: "2026-10-16", shares: 0 }]),
    );
    vi.mocked(getStockChipFlows).mockResolvedValue(flows());
    await ready();
    expect(stats()[copy.sharesOfCapital]).toBe("—");
    expect(stats()[copy.turnoverRatio]).toBe("—");
  });

  it("shows a net-selling main force with its own sign", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(flows({ main_force_net: -500_000 }));
    await ready();
    expect(stats()[copy.mainForceNet]).toContain("▼");
    expect(stats()[copy.mainForceNet]).toContain("500");
  });

  it("switches the window, asking the server again with a different start date", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(flows());
    await ready();
    const first = vi.mocked(getStockChipFlows).mock.calls[0]?.[2]?.startDate;
    fireEvent.click(screen.getByRole("button", { name: copy.brokerLookbackWindow["10d"] }));
    await screen.findAllByText(copy.mainForceNet);
    await new Promise((r) => setTimeout(r, 0));
    const calls = vi.mocked(getStockChipFlows).mock.calls;
    expect(calls.length).toBe(2);
    expect(calls[1]?.[2]?.startDate).not.toBe(first);
    expect(
      screen
        .getByRole("button", { name: copy.brokerLookbackWindow["10d"] })
        .getAttribute("aria-pressed"),
    ).toBe("true");
  });

  it("toggles between the buy and sell tables, and says when one side is empty", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(flows({ buy_top: [] }));
    await ready();
    expect(screen.getByRole("heading", { name: copy.buyTop15 })).toBeTruthy();
    expect(screen.getByText(copy.empty)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: copy.sellTop15 }));
    expect(screen.getByRole("heading", { name: copy.sellTop15 })).toBeTruthy();
    expect(screen.getByText("賣方一")).toBeTruthy();
    expect(screen.queryByText(copy.empty)).toBeNull();
  });
});

describe("ChipFlowsBody top table", () => {
  it("renders locked and incomplete rows as placeholders, never their values", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(
      flows({
        buy_top: [
          top(1),
          top(2, { locked: true, broker_name: null, net: null, buy: null, sell: null }),
          top(3, { net: null }),
          top(4, { buy: null }),
          top(5, { sell: null }),
        ],
      }),
    );
    const c = await ready();
    const rows = Array.from(c.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(5);
    expect(rows[0]?.textContent).toContain("券商1");
    for (const i of [1, 2, 3, 4]) {
      expect(rows[i]?.textContent).toContain("••••");
      expect(rows[i]?.textContent).toContain(`#${i + 1}`);
    }
    expect(c.querySelector("tbody")?.innerHTML).toMatchSnapshot();
  });

  it("shows each broker's share of the reported volume, and 0% with no volume", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(flows({ buy_top: [top(1)] }));
    const c = await ready();
    expect(c.querySelector("tbody tr")?.textContent).toContain("5.00%");
    cleanup();
    vi.mocked(getStockChipFlows).mockResolvedValue(flows({ buy_top: [top(1)], total_volume: 0 }));
    const again = await ready();
    expect(again.querySelector("tbody tr")?.textContent).toContain("0.00%");
  });

  it("colours a net by direction and prefixes its glyph", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(
      flows({ buy_top: [top(1, { net: 5000 }), top(2, { net: -5000 }), top(3, { net: 0 })] }),
    );
    const c = await ready();
    const cells = Array.from(c.querySelectorAll<HTMLElement>("tbody tr td:nth-child(2)"));
    expect(cells[0]?.textContent).toContain("▲");
    expect(cells[1]?.textContent).toContain("▼");
    expect(cells.map((cell) => cell.style.color)).toMatchSnapshot();
  });

  it("collapses to eight rows and expands the rest in a modal", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(
      flows({ buy_top: Array.from({ length: 12 }, (_, i) => top(i + 1)) }),
    );
    const c = await ready();
    expect(c.querySelectorAll("tbody tr")).toHaveLength(8);
    fireEvent.click(screen.getByRole("button", { name: copy.expandTo.replace("{count}", "12") }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.querySelectorAll("tbody tr")).toHaveLength(12);
  });

  it("offers no expand button for eight rows or fewer", async () => {
    vi.mocked(getStockChipFlows).mockResolvedValue(
      flows({ buy_top: Array.from({ length: 8 }, (_, i) => top(i + 1)) }),
    );
    await ready();
    expect(screen.queryByRole("button", { name: /展開|expand/i })).toBeNull();
  });
});
