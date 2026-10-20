// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TwMarketRankingRows } from "@/components/market/tw-market-ranking-rows";
import { messages, renderWithIntl, withIntl } from "@/test/render";
import type { TwRankingRow } from "@/types/market/rankings";

vi.mock("@/hooks/use-symbol-quotes", () => ({ useSymbolQuotes: () => ({ quotes: [] }) }));
vi.mock("@/components/stock/watchlist-star-button", () => ({ WatchlistStarButton: () => null }));
vi.mock("@/i18n/navigation", async () => ({
  Link: (await import("@/test/link-stub")).LinkStub,
}));

afterEach(cleanup);

function row(symbol: string, extraValue: number, extraLabel: string): TwRankingRow {
  return {
    symbol,
    name: `名稱${symbol}`,
    price: null,
    change: null,
    change_pct: null,
    volume: null,
    extra_value: extraValue,
    extra_label: extraLabel,
  };
}

const rows = [row("1101", -3, "-3 天"), row("2330", 5, "+5 天"), row("2317", 1, "+1 天")];

function rowLabels(): string[] {
  return screen.getAllByRole("group").map((item) => item.getAttribute("aria-label") ?? "");
}

describe("TwMarketRankingRows", () => {
  it("renders the extra column with the signed colour token and announces it on the row", () => {
    renderWithIntl(<TwMarketRankingRows rows={rows} market="TW" extraColumnLabel="連買天數" />);
    const items = screen.getAllByRole("group");
    expect(items[0]?.getAttribute("aria-label")).toContain("-3 天");
    const down = within(items[0] as HTMLElement).getByText("-3 天");
    const up = within(items[1] as HTMLElement).getByText("+5 天");
    expect(down.getAttribute("style")).toContain("var(--down)");
    expect(up.getAttribute("style")).toContain("var(--up)");
  });

  it("sorts by the extra column and keeps that sort when the rows prop changes", () => {
    const { rerender } = renderWithIntl(
      <TwMarketRankingRows rows={rows} market="TW" extraColumnLabel="連買天數" />,
    );
    fireEvent.click(screen.getByRole("button", { name: /連買天數/ }));
    expect(rowLabels()[0]).toContain("2330");
    rerender(
      withIntl(<TwMarketRankingRows rows={[...rows]} market="TW" extraColumnLabel="連買天數" />),
    );
    expect(rowLabels()[0]).toContain("2330");
  });
});
