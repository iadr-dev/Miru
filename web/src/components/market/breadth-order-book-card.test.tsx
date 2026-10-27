// @vitest-environment jsdom
import { cleanup, fireEvent, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { BreadthOrderBookCard } from "@/components/market/breadth-order-book-card";
import { messages, renderWithIntl } from "@/test/render";
import type {
  AdvanceDeclineResponse,
  IndexContributionResponse,
  IndexContributionRow,
} from "@/types/market/breadth";

afterEach(cleanup);

const copy = messages.market.cards.indexContribution;

const breadth: AdvanceDeclineResponse = {
  market: "TW",
  trade_date: null,
  data_available: false,
  advancing_count: 0,
  declining_count: 0,
  unchanged_count: 0,
  buckets: {
    down_over_5pct: 0,
    down_3_to_5pct: 0,
    down_2_to_3pct: 0,
    down_1_to_2pct: 0,
    down_0_to_1pct: 0,
    unchanged: 0,
    up_0_to_1pct: 0,
    up_1_to_2pct: 0,
    up_2_to_3pct: 0,
    up_3_to_5pct: 0,
    up_over_5pct: 0,
  },
};

function row(rank: number, symbol: string, name: string, value: number): IndexContributionRow {
  return {
    rank,
    symbol,
    stock_name: name,
    weight_per: 1,
    pct_change: value / 10,
    contribution_estimate: value,
    locked: false,
  };
}

function contribution(count: number): IndexContributionResponse {
  const side = (sign: number): IndexContributionRow[] =>
    Array.from({ length: count }, (_, i) =>
      row(i + 1, `${sign > 0 ? 2330 : 2454}${i}`, sign > 0 ? "台積電股份有限公司" : "聯發科", sign),
    );
  return {
    market: "TW",
    trade_date: "2026-10-27",
    prior_trade_date: "2026-10-22",
    data_available: true,
    unavailable_reason: null,
    contribution_caveat: "x",
    free_row_count: 2,
    positive_contributors: side(1),
    negative_contributors: side(-1),
  };
}

function renderCard(count: number): void {
  renderWithIntl(
    <BreadthOrderBookCard breadth={breadth} indexContribution={contribution(count)} market="TW" />,
  );
}

describe("BreadthOrderBookCard index contribution — what the numbers are", () => {
  it("says the weight is a market-value share, not free-float adjusted, and not the exchange's", () => {
    renderCard(3);
    const note = screen.getByText(copy.estimateNote);
    expect(copy.estimateNote).toContain("已發行股數");
    expect(copy.estimateNote).toContain("未做流通調整");
    expect(copy.estimateNote).toContain("並非證交所公布");
    expect(note.tagName).toBe("P");
  });
});

describe("BreadthOrderBookCard index contribution — responsive layout", () => {
  it("stacks the two ranking columns on a phone and puts them side by side from the sm breakpoint", () => {
    renderCard(3);
    const grid = screen.getByText(copy.positive).closest("div")?.parentElement;
    expect(grid?.className).toContain("grid-cols-1");
    expect(grid?.className).toContain("sm:grid-cols-2");
    expect(grid?.className).not.toMatch(/(^|\s)grid-cols-2/);
  });

  it("keeps the full stock name reachable when a narrow row truncates it", () => {
    renderCard(3);
    const names = screen.getAllByTitle("台積電股份有限公司 23300");
    expect(names[0]?.className).toContain("truncate");
  });

  it("offers a 44px expand control that opens the full list in the shared modal", () => {
    renderCard(8);
    const expand = screen.getByRole("button", { name: /展開完整 8 筆/ });
    expect(expand.className).toContain("min-h-11");
    expect(expand.className).toContain("w-full");
    fireEvent.click(expand);
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("max-h-[85dvh]");
    expect(dialog.className).toContain("w-full");
    expect(dialog.querySelector(".overflow-auto")).not.toBeNull();
  });
});
