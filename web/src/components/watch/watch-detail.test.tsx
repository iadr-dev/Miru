// @vitest-environment jsdom
import { cleanup, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { WatchDetail } from "@/components/watch/watch-detail";
import { usePriceHistory } from "@/hooks/use-stock-data";
import { messages, renderWithIntl } from "@/test/render";
import type { StrategyWatchItem } from "@/types/strategy-watch";

vi.mock("@/hooks/use-stock-data", () => ({ usePriceHistory: vi.fn() }));
vi.mock("@/i18n/navigation", async () => ({
  Link: (await import("@/test/link-stub")).LinkStub,
}));
vi.mock("recharts", async (importOriginal) =>
  (await import("@/test/fixed-size-recharts")).withFixedSizeContainer(
    await importOriginal<typeof import("recharts")>(),
  ),
);

const t = messages.watch;

function item(over: Partial<StrategyWatchItem> = {}): StrategyWatchItem {
  return {
    symbol: "2330",
    name: "台積電",
    strategy: "volatility_squeeze",
    industry: null,
    close: 860,
    upper_band: 868,
    lower_band: 820,
    low_20d: null,
    range_high_20d: null,
    range_low_20d: null,
    flag_high: null,
    flag_low: null,
    sector_strength_pct: null,
    foreign_streak: 0,
    trust_streak: 0,
    large_holder_pct: null,
    large_holder_change_4w: null,
    holders_change_4w_pct: null,
    margin_change_20d_pct: null,
    headline: null,
    days_on_list: 2,
    first_listed: "2026-10-15",
    latest_slot: "10:00",
    price: 872,
    conditions: [
      { key: "price_above_upper_band", kind: "trigger", met: true, observed: 872, required: 868 },
      { key: "volume_ratio_at_least", kind: "trigger", met: false, observed: 1.1, required: 1.5 },
      { key: "after_opening_window", kind: "guard", met: true, observed: null, required: null },
    ],
    all_met: false,
    alerted_at: null,
    close_confirmed: false,
    ...over,
  };
}

function history(closes: number[] | null): void {
  vi.mocked(usePriceHistory).mockReturnValue({
    data:
      closes === null
        ? undefined
        : {
            rows: closes.map((close, i) => ({
              trade_date: `2026-10-${String(i + 1).padStart(2, "0")}`,
              close,
            })),
          },
  } as unknown as ReturnType<typeof usePriceHistory>);
}

function facts(): string[] {
  const section = document.querySelector("section[aria-labelledby='watch-context']");
  return Array.from(section?.querySelectorAll("li") ?? []).map((li) => li.textContent ?? "");
}

beforeEach(() => {
  history(null);
});
afterEach(cleanup);

describe("WatchDetail", () => {
  it("asks to pick a stock when none is selected", () => {
    const { container } = renderWithIntl(<WatchDetail item={null} masked={false} />);
    expect(container.textContent).toBe(t.detail.select);
  });

  it("shows the header, conditions with observed/required, guards, and a chart skeleton", () => {
    const { container } = renderWithIntl(<WatchDetail item={item()} masked={false} />);
    expect(screen.getByText("台積電")).toBeTruthy();
    expect(screen.getByText("10:00")).toBeTruthy();
    expect(screen.queryByText(t.masked)).toBeNull();
    expect(container.querySelector("section[aria-labelledby='watch-context']")).toBeNull();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("falls back to the symbol when the stock has no name", () => {
    renderWithIntl(<WatchDetail item={item({ name: null })} masked={false} />);
    expect(screen.getAllByText("2330").length).toBeGreaterThan(0);
  });

  it("says when the stock has not been checked intraday and omits guards", () => {
    const { container } = renderWithIntl(
      <WatchDetail item={item({ conditions: [], latest_slot: null })} masked={false} />,
    );
    expect(screen.getByText(t.detail.notChecked)).toBeTruthy();
    expect(screen.queryByText(t.detail.guards)).toBeNull();
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("shows the masked note when the list is masked", () => {
    renderWithIntl(<WatchDetail item={item()} masked />);
    expect(screen.getByText(t.masked)).toBeTruthy();
  });

  it("shows a condition without observed and required values bare", () => {
    renderWithIntl(
      <WatchDetail
        item={item({
          conditions: [
            {
              key: "price_above_upper_band",
              kind: "trigger",
              met: true,
              observed: null,
              required: 5,
            },
            {
              key: "volume_ratio_at_least",
              kind: "trigger",
              met: false,
              observed: null,
              required: null,
            },
          ],
        })}
        masked={false}
      />,
    );
    expect(screen.getAllByText(/\//)).toHaveLength(1);
  });

  it("draws the chart with the trigger level and the floor when history has loaded", () => {
    history([850, 855, 860, 870]);
    const { container } = renderWithIntl(
      <WatchDetail item={item({ range_low_20d: 800 })} masked={false} />,
    );
    expect(container.querySelector("figure")).not.toBeNull();
    expect(container.querySelectorAll(".recharts-reference-line")).toHaveLength(2);
  });

  it("does not draw the floor for strategies that watch a lower level", () => {
    history([850, 855, 860, 870]);
    const { container } = renderWithIntl(
      <WatchDetail
        item={item({ strategy: "oversold_bounce", range_low_20d: 800 })}
        masked={false}
      />,
    );
    expect(container.querySelectorAll(".recharts-reference-line")).toHaveLength(1);
  });

  it("omits the trigger line when the level is unknown", () => {
    history([850, 855, 860, 870]);
    const { container } = renderWithIntl(
      <WatchDetail item={item({ upper_band: null })} masked={false} />,
    );
    expect(container.querySelectorAll(".recharts-reference-line")).toHaveLength(0);
  });
});

describe("WatchDetail context facts", () => {
  it("lists every kind of fact in order, with a surging margin flagged", () => {
    const { container } = renderWithIntl(
      <WatchDetail
        item={item({
          industry: "半導體業",
          sector_strength_pct: 92.6,
          foreign_streak: 3,
          trust_streak: -2,
          large_holder_pct: 63.456,
          large_holder_change_4w: 0.123,
          margin_change_20d_pct: 25.04,
          headline: "法說會",
        })}
        masked={false}
      />,
    );
    expect(facts()).toEqual([
      "半導體業 類股強度 93",
      "外資連 3 日買超",
      "投信連 2 日賣超",
      "千張大戶 63.5%（4 週 +0.12 個百分點）",
      "融資 20 日 +25.0%（融資增幅偏大）",
      "今日重訊：法說會",
    ]);
    expect(container.innerHTML).toMatchSnapshot();
  });

  it("shows a falling large-holder share and a calm margin change without the surge note", () => {
    renderWithIntl(
      <WatchDetail
        item={item({
          large_holder_pct: 50,
          large_holder_change_4w: -1.5,
          margin_change_20d_pct: -3.26,
        })}
        masked={false}
      />,
    );
    expect(facts()).toEqual(["千張大戶 50.0%（4 週 -1.50 個百分點）", "融資 20 日 -3.3%"]);
  });

  it("treats a margin change of exactly 20 as calm and 0 as a plain +0.0", () => {
    renderWithIntl(<WatchDetail item={item({ margin_change_20d_pct: 20 })} masked={false} />);
    expect(facts()).toEqual(["融資 20 日 +20.0%"]);
    cleanup();
    renderWithIntl(<WatchDetail item={item({ margin_change_20d_pct: 0 })} masked={false} />);
    expect(facts()).toEqual(["融資 20 日 +0.0%"]);
  });

  it("needs a streak of at least two days, in either direction", () => {
    renderWithIntl(
      <WatchDetail item={item({ foreign_streak: 1, trust_streak: -1 })} masked={false} />,
    );
    expect(facts()).toEqual([]);
    cleanup();
    renderWithIntl(
      <WatchDetail item={item({ foreign_streak: -2, trust_streak: 2 })} masked={false} />,
    );
    expect(facts()).toEqual(["外資連 2 日賣超", "投信連 2 日買超"]);
  });

  it("needs both the industry and its strength for the sector fact", () => {
    renderWithIntl(
      <WatchDetail
        item={item({ industry: "半導體業", sector_strength_pct: null })}
        masked={false}
      />,
    );
    expect(facts()).toEqual([]);
    cleanup();
    renderWithIntl(
      <WatchDetail item={item({ industry: null, sector_strength_pct: 80 })} masked={false} />,
    );
    expect(facts()).toEqual([]);
    cleanup();
    renderWithIntl(
      <WatchDetail item={item({ industry: "", sector_strength_pct: 80 })} masked={false} />,
    );
    expect(facts()).toEqual([]);
  });

  it("needs both large-holder numbers, and ignores an empty headline", () => {
    renderWithIntl(
      <WatchDetail
        item={item({ large_holder_pct: 60, large_holder_change_4w: null, headline: "" })}
        masked={false}
      />,
    );
    expect(facts()).toEqual([]);
    cleanup();
    renderWithIntl(
      <WatchDetail
        item={item({ large_holder_pct: null, large_holder_change_4w: 1 })}
        masked={false}
      />,
    );
    expect(facts()).toEqual([]);
  });
});
