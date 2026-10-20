// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ForeignShareholdingBody } from "@/components/stock/chip/foreign-holdings";
import { getForeignShareholding } from "@/lib/api/stock-detail";
import { messages, renderWithIntl } from "@/test/render";

vi.mock("recharts", async (importOriginal) =>
  (await import("@/test/fixed-size-recharts")).withFixedSizeContainer(
    await importOriginal<typeof import("recharts")>(),
  ),
);
vi.mock("@/lib/api/stock-detail", () => ({ getForeignShareholding: vi.fn() }));
vi.mock("@/hooks/use-stock-data", () => ({
  useInstitutionalFlow: () => ({
    isLoading: false,
    isError: false,
    data: {
      rows: [
        {
          symbol: "2330",
          market: "TW",
          trade_date: "2026-01-02",
          investor: "Investment_Trust",
          buy: 3000,
          sell: 1000,
        },
        {
          symbol: "2330",
          market: "TW",
          trade_date: "2026-01-03",
          investor: "Dealer_self",
          buy: 500,
          sell: 100,
        },
      ],
    },
  }),
  usePriceHistory: () => ({ data: { rows: [{ trade_date: "2026-01-02", close: 100 }] } }),
}));

afterEach(cleanup);

const copy = messages.stock.chipAnalysis;

function holdingRow(tradeDate: string, shares: number): Record<string, unknown> {
  return {
    symbol: "2330",
    market: "TW",
    trade_date: tradeDate,
    stock_name: "台積電",
    international_code: "TW0002330008",
    foreign_investment_remaining_shares: 1,
    foreign_investment_shares: shares,
    foreign_investment_remain_ratio: 1,
    foreign_investment_shares_ratio: 70,
    foreign_investment_upper_limit_ratio: 100,
    chinese_investment_upper_limit_ratio: 10,
    number_of_shares_issued: 25_930_000_000,
  };
}

async function renderBody(): Promise<void> {
  vi.mocked(getForeignShareholding).mockResolvedValue({
    source_id: "x",
    currentness_caveat: "",
    health_status: "ok",
    rows: [holdingRow("2026-01-02", 5_000_000), holdingRow("2026-01-03", 6_000_000)],
  } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderWithIntl(
    <QueryClientProvider client={client}>
      <ForeignShareholdingBody symbol="2330" market="TW" />
    </QueryClientProvider>,
  );
  await screen.findAllByText(/外資/);
}

function legendLabel(text: string): HTMLElement {
  const spans = document.querySelectorAll<HTMLElement>(".recharts-legend-wrapper span[style]");
  const found = [...spans].find((el) => el.textContent === text && el.style.opacity !== "");
  if (!found) throw new Error(`legend label ${text} missing`);
  return found;
}

describe("ForeignShareholdingBody legend", () => {
  it("labels each series, marking the 投信/自營商 ones as estimates", async () => {
    await renderBody();
    expect(legendLabel("外資")).toBeTruthy();
    expect(legendLabel(copy.estimateLegend.replace("{label}", "投信"))).toBeTruthy();
    expect(legendLabel(copy.estimateLegend.replace("{label}", "自營商"))).toBeTruthy();
  });

  it("dims a series' label once its legend entry is clicked, and restores it on a second click", async () => {
    await renderBody();
    const trust = copy.estimateLegend.replace("{label}", "投信");
    expect(legendLabel(trust).style.opacity).toBe("1");
    const item = legendLabel(trust).closest("li");
    if (!item) throw new Error("legend item missing");
    fireEvent.click(item);
    expect(legendLabel(trust).style.opacity).toBe("0.4");
    fireEvent.click(legendLabel(trust).closest("li") as HTMLElement);
    expect(legendLabel(trust).style.opacity).toBe("1");
  });
});
