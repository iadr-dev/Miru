// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AiPanel } from "@/components/stock/ai-panel";
import { getStockScore, getStockSummary, getTrendScore } from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";
import type { Market } from "@/types/settings";
import type { StockScoreResponse, TrendScoreData } from "@/types/stock/ai";

vi.mock("recharts", async (importOriginal) =>
  (await import("@/test/fixed-size-recharts")).withFixedSizeContainer(
    await importOriginal<typeof import("recharts")>(),
  ),
);
vi.mock("@/lib/api/stock-detail", () => ({
  getTrendScore: vi.fn(),
  getStockScore: vi.fn(),
  getStockSummary: vi.fn(),
}));

const copy = messages.stock.ai;

function dimension(
  name: string,
  value: number | null,
  over: { locked?: boolean } = {},
): TrendScoreData["dimensions"][number] {
  return { name, value, description: "", unavailable_reason: null, locked: false, ...over };
}

function trend(over: Partial<TrendScoreData> = {}): TrendScoreData {
  return {
    found: true,
    symbol: "2330",
    market: "TW",
    as_of: "2026-10-16",
    composite: 72.34,
    dimensions: [
      dimension("technical", 80),
      dimension("chip_flow", 55),
      dimension("margin_credit", 20),
      dimension("broker_concentration", null, { locked: true }),
      dimension("sector_rotation", 61),
      dimension("taiex_correlation", 45),
    ],
    unavailable_dimensions: [],
    history: [
      { trade_date: "2026-10-14", composite: 30, close: 100 },
      { trade_date: "2026-10-15", composite: 50, close: 101 },
      { trade_date: "2026-10-16", composite: 70, close: 102 },
    ],
    non_trading_caveat: "",
    message: "",
    ...over,
  };
}

function mount(market: Market = "TW", score?: StockScoreResponse): HTMLElement {
  vi.mocked(getStockScore).mockResolvedValue(
    score ?? { found: false, symbol: "2330", market, profile: "swing", message: "" },
  );
  vi.mocked(getStockSummary).mockResolvedValue({
    found: false,
    symbol: "2330",
    market,
    as_of: null,
    headline: "",
    narrative: "",
    swot: { strengths: [], weaknesses: [], opportunities: [], threats: [] },
    non_trading_caveat: "",
    message: "",
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return renderWithIntl(
    <QueryClientProvider client={client}>
      <AiPanel symbol="2330" market={market} />
    </QueryClientProvider>,
  ).container;
}

async function trendCard(
  data: TrendScoreData | Error | "pending",
  market: Market = "TW",
): Promise<HTMLElement> {
  if (data === "pending") vi.mocked(getTrendScore).mockReturnValue(new Promise(() => undefined));
  else if (data instanceof Error) vi.mocked(getTrendScore).mockRejectedValue(data);
  else vi.mocked(getTrendScore).mockResolvedValue(data);
  mount(market);
  await screen.findByText(copy.footerCaveat);
  await new Promise((r) => setTimeout(r, 0));
  const section = screen.queryByRole("heading", { name: copy.trendScoreTitle })?.closest("section");
  return (section ?? document.body) as HTMLElement;
}

function dimensionRows(card: HTMLElement): string[] {
  const grid = card.querySelector("div.grid");
  return Array.from(grid?.querySelectorAll(":scope > div > div") ?? []).map(
    (row) => row.textContent ?? "",
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("AiPanel trend score card", () => {
  it("shows a skeleton while loading, and no card", async () => {
    await trendCard("pending");
    expect(screen.queryByRole("heading", { name: copy.trendScoreTitle })).toBeNull();
  });

  it("shows a retryable error", async () => {
    await trendCard(new Error("x"));
    expect(screen.getAllByRole("alert").length).toBeGreaterThan(0);
    expect(screen.queryByRole("heading", { name: copy.trendScoreTitle })).toBeNull();
  });

  it("says why when the score was not found, with the server's message or the fallback", async () => {
    const card = await trendCard(trend({ found: false, message: "資料不足", as_of: null }));
    expect(card.textContent).toContain("資料不足");
    expect(card.textContent).not.toContain("2026-10-16");
    cleanup();
    const again = await trendCard(trend({ found: false, message: "", as_of: "2026-10-16" }));
    expect(again.textContent).toContain(copy.trendScoreUnavailable);
    expect(again.textContent).toContain(copy.asOfDate.replace("{date}", "2026-10-16"));
  });

  it("shows the composite with its unit and the as-of date, with the chart and bars", async () => {
    const card = await trendCard(trend());
    expect(card.textContent).toContain("72.3");
    expect(card.textContent).toContain(copy.scoreUnit);
    expect(card.textContent).toContain(copy.asOfDate.replace("{date}", "2026-10-16"));
    expect(card.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(3);
    expect(card.querySelectorAll(".recharts-reference-line")).toHaveLength(2);
    expect(card.querySelectorAll(".recharts-yAxis")).toHaveLength(2);
    expect(card.querySelector(".recharts-line")).not.toBeNull();
    expect(card.innerHTML).toMatchSnapshot();
  });

  it("colours the big number by band: bearish, neutral, bullish, and plain when unknown", async () => {
    const colourOf = (card: HTMLElement): string =>
      card.querySelector<HTMLElement>("div.font-num")?.style.color ?? "";
    expect(colourOf(await trendCard(trend({ composite: 39.9 })))).toBe("var(--down)");
    cleanup();
    expect(colourOf(await trendCard(trend({ composite: 40 })))).toBe("var(--warn)");
    cleanup();
    expect(colourOf(await trendCard(trend({ composite: 59.9 })))).toBe("var(--warn)");
    cleanup();
    expect(colourOf(await trendCard(trend({ composite: 60 })))).toBe("var(--up)");
    cleanup();
    const none = await trendCard(trend({ composite: null }));
    expect(colourOf(none)).toBe("var(--text)");
    expect(none.querySelector("div.font-num")?.textContent).toBe("—");
  });

  it("without a close series, draws no price axis or line", async () => {
    const card = await trendCard(
      trend({
        history: [
          { trade_date: "2026-10-15", composite: 50, close: null },
          { trade_date: "2026-10-16", composite: 70, close: null },
        ],
      }),
    );
    expect(card.querySelectorAll(".recharts-yAxis")).toHaveLength(1);
    expect(card.querySelector(".recharts-line")).toBeNull();
    expect(card.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(2);
  });

  it("skips history points with no composite, and says so when none remain", async () => {
    const some = await trendCard(
      trend({
        history: [
          { trade_date: "2026-10-15", composite: null, close: 100 },
          { trade_date: "2026-10-16", composite: 70, close: 101 },
        ],
      }),
    );
    expect(some.querySelectorAll(".recharts-bar-rectangle")).toHaveLength(1);
    cleanup();
    const none = await trendCard(
      trend({ history: [{ trade_date: "2026-10-16", composite: null, close: 101 }] }),
    );
    expect(none.textContent).toContain(copy.noHistoryYet);
    expect(none.querySelector(".recharts-wrapper")).toBeNull();
    cleanup();
    const empty = await trendCard(trend({ history: [] }));
    expect(empty.textContent).toContain(copy.noHistoryYet);
  });
});

function scoreWithRevenueFlow(description: string): StockScoreResponse {
  return {
    found: true,
    symbol: "2330",
    market: "TW",
    profile: "swing",
    message: "",
    result: {
      symbol: "2330",
      symbol_type: "stock",
      composite: 62.9,
      band: "偏多",
      breakdown: {
        revenue_flow: {
          name: "revenue_flow",
          percentile: 66.7,
          description,
          unavailable_reason: null,
        },
      },
    },
  };
}

describe("AiPanel score factors", () => {
  beforeEach(() => {
    vi.mocked(getTrendScore).mockReturnValue(new Promise(() => undefined));
  });

  it("captions revenue_flow with the description the backend sent, not the fixed translation", async () => {
    const description = "反映三大法人 20日淨買超與月營收 YoY（等權平均百分位）";
    const container = mount("TW", scoreWithRevenueFlow(description));
    expect(await screen.findByText(description)).toBeTruthy();
    expect(container.textContent).not.toContain(copy.factorCaption.revenue_flow);
  });

  it("falls back to the translated caption when the backend sends no description", async () => {
    mount("TW", scoreWithRevenueFlow(""));
    expect(await screen.findByText(copy.factorCaption.revenue_flow)).toBeTruthy();
  });
});

describe("AiPanel trend dimensions", () => {
  it("lists the Taiwan dimensions in two columns, with a locked 分點 teaser", async () => {
    const card = await trendCard(trend());
    const rows = dimensionRows(card);
    expect(rows).toHaveLength(6);
    expect(rows[0]).toContain(copy.dimension.technical);
    expect(rows[0]).toContain("80");
    expect(rows[0]).toContain(copy.dimensionCaption.technical);
    expect(rows[3]).toContain(copy.dimension.broker_concentration);
    expect(rows[3]).toContain(copy.dimensionLocked);
    expect(card.querySelector(".border-dashed")).not.toBeNull();
  });

  it("uses the US dimension set for a US stock and ignores Taiwan-only names", async () => {
    const card = await trendCard(
      trend({
        market: "US",
        dimensions: [
          dimension("technical", 50),
          dimension("institutional_ownership", 60),
          dimension("insider_sentiment", 70),
          dimension("analyst_revision", 40),
          dimension("sector_momentum", 30),
          dimension("taiex_correlation", 20),
          dimension("chip_flow", 99),
        ],
      }),
      "US",
    );
    const rows = dimensionRows(card);
    expect(rows).toHaveLength(6);
    expect(card.textContent).not.toContain(copy.dimension.chip_flow);
    expect(card.innerHTML).toMatchSnapshot();
  });

  it("skips dimensions the batch did not compute, but keeps an unlocked empty 分點 with its caption", async () => {
    const card = await trendCard(
      trend({
        dimensions: [
          dimension("technical", 80),
          dimension("chip_flow", null),
          dimension("broker_concentration", null),
        ],
      }),
    );
    const rows = dimensionRows(card);
    expect(rows).toHaveLength(2);
    expect(rows[1]).toContain(copy.dimensionUnavailable);
    expect(rows[1]).not.toContain(copy.dimensionLocked);
    expect(card.querySelector(".border-dashed")).toBeNull();
  });

  it("shows the value and a caption for an unlocked 分點 that has data", async () => {
    const card = await trendCard(trend({ dimensions: [dimension("broker_concentration", 66.6)] }));
    const rows = dimensionRows(card);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toContain("67");
    expect(rows[0]).toContain(copy.dimensionCaption.broker_concentration);
  });
});
