// @vitest-environment jsdom
import { cleanup, fireEvent, screen, within } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LeaderboardCard } from "@/components/market/leaderboard-card";
import { messages, renderWithIntl, withIntl } from "@/test/render";
import type { ScoreResult } from "@/types/market/rankings";

vi.mock("@/hooks/use-symbol-quotes", () => ({ useSymbolQuotes: () => ({ quotes: [] }) }));
vi.mock("@/components/stock/watchlist-star-button", () => ({ WatchlistStarButton: () => null }));
vi.mock("@/i18n/navigation", async () => ({
  Link: (await import("@/test/link-stub")).LinkStub,
}));

afterEach(cleanup);

const copy = messages.market.cards.leaderboard;

function result(symbol: string, composite: number | null, band: ScoreResult["band"]): ScoreResult {
  return { symbol, composite, band };
}

const results = [
  result("2330", 72, "偏多"),
  result("1101", 35, "偏空"),
  result("2317", null, "中性"),
];

function card(list: ScoreResult[], asOf: string | null): ReactElement {
  return (
    <LeaderboardCard
      results={list}
      asOf={asOf}
      direction="bullish"
      onDirectionChange={() => undefined}
      market="TW"
      assetClass="stock"
    />
  );
}

describe("LeaderboardCard score column", () => {
  it("shows the composite in the band colour and announces band + score on the row", () => {
    renderWithIntl(card(results, null));
    const rows = screen.getAllByRole("group");
    expect(rows[0]?.getAttribute("aria-label")).toContain(`偏多 ${copy.scoreAria} 72`);
    const bull = within(rows[0] as HTMLElement).getByText("72");
    expect(bull.getAttribute("style")).toContain("var(--up)");
    const bear = within(rows[1] as HTMLElement).getByText("35");
    expect(bear.getAttribute("style")).toContain("var(--down)");
  });

  it("renders a dash and the empty aria text for a null composite", () => {
    renderWithIntl(card(results, null));
    const rows = screen.getAllByRole("group");
    expect(rows[2]?.getAttribute("aria-label")).toContain(`中性 ${copy.scoreAria} ${copy.empty}`);
    const pill = within(rows[2] as HTMLElement)
      .getAllByText("—")
      .at(-1);
    expect(pill?.getAttribute("style")).toContain("var(--text-muted)");
    expect(pill?.className).toContain("font-bold");
  });

  it("keeps the score sort across a re-render with new props", () => {
    const ascending = [results[1], results[0]].filter((r) => r !== undefined);
    const { rerender } = renderWithIntl(card(ascending, null));
    fireEvent.click(screen.getByRole("button", { name: new RegExp(copy.columnScore) }));
    const first = (): string => screen.getAllByRole("group")[0]?.getAttribute("aria-label") ?? "";
    expect(first()).toContain("2330");
    rerender(withIntl(card([...ascending], "2026-01-02")));
    expect(first()).toContain("2330");
  });
});
