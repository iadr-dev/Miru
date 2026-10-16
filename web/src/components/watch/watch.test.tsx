// @vitest-environment jsdom
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { pipelineSteps } from "@/components/watch/run-status";
import {
  distanceToLevel,
  initialStrategy,
  keyLevel,
  triggersMet,
} from "@/components/watch/watch-format";
import { WatchTable } from "@/components/watch/watch-table";
import { renderWithIntl } from "@/test/render";
import {
  STRATEGY_KEYS,
  type StrategyWatchItem,
  type StrategyWatchToday,
} from "@/types/strategy-watch";

function item(overrides: Partial<StrategyWatchItem> = {}): StrategyWatchItem {
  return {
    symbol: "2330",
    name: "台積電",
    strategy: "volatility_squeeze",
    industry: "半導體業",
    close: 860,
    upper_band: 868,
    lower_band: 820,
    low_20d: null,
    range_high_20d: null,
    range_low_20d: null,
    flag_high: null,
    flag_low: null,
    sector_strength_pct: 93,
    foreign_streak: 3,
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
    ...overrides,
  };
}

describe("watch-format", () => {
  it("picks the level each strategy waits on", () => {
    expect(keyLevel(item())).toEqual({ key: "upper_band", value: 868 });
    expect(keyLevel(item({ strategy: "oversold_bounce" }))).toEqual({
      key: "lower_band",
      value: 820,
    });
    expect(keyLevel(item({ strategy: "momentum_flag", flag_high: 60.3 })).key).toBe("flag_high");
  });

  it("measures the distance from the latest price, else the last close", () => {
    expect(distanceToLevel(item())).toBeCloseTo(((872 - 868) / 868) * 100);
    expect(distanceToLevel(item({ price: null }))).toBeCloseTo(((860 - 868) / 868) * 100);
    expect(distanceToLevel(item({ upper_band: null }))).toBeNull();
  });

  it("counts triggers only, not guards", () => {
    expect(triggersMet(item())).toEqual({ met: 1, total: 2 });
  });

  it("opens the asked-for tab, else the first with stocks", () => {
    const counts = { volatility_squeeze: 0, oversold_bounce: 3 };
    expect(initialStrategy(counts, STRATEGY_KEYS, "momentum_flag")).toBe("momentum_flag");
    expect(initialStrategy(counts, STRATEGY_KEYS, "nonsense")).toBe("oversold_bounce");
    expect(initialStrategy({}, STRATEGY_KEYS, null)).toBe("volatility_squeeze");
  });
});

describe("WatchTable", () => {
  it("shows the stock, its key level and how many conditions are met, and selects on click", () => {
    const onSelect = vi.fn();
    renderWithIntl(
      <WatchTable
        items={[item({ alerted_at: "09:50", close_confirmed: true })]}
        caption="波動率擠壓 今日清單"
        selected={null}
        onSelect={onSelect}
      />,
    );
    expect(screen.getByText("台積電")).toBeTruthy();
    expect(screen.getByText(/布林上軌 \+0\.5%/)).toBeTruthy();
    expect(screen.getByRole("img", { name: "1/2 個條件成立" })).toBeTruthy();
    expect(screen.getByText("已通知 09:50")).toBeTruthy();
    expect(screen.getByText("收盤確認")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /台積電/ }));
    expect(onSelect).toHaveBeenCalledWith("2330");
  });

  it("says when a strategy has no stock today", () => {
    renderWithIntl(<WatchTable items={[]} caption="x" selected={null} onSelect={vi.fn()} />);
    expect(screen.getByText("這個策略今天沒有符合的股票。")).toBeTruthy();
  });

  it("marks a stock not yet checked intraday", () => {
    renderWithIntl(
      <WatchTable
        items={[item({ latest_slot: null, conditions: [] })]}
        caption="x"
        selected={null}
        onSelect={vi.fn()}
      />,
    );
    expect(screen.getByText("尚未檢查")).toBeTruthy();
  });
});

describe("pipelineSteps", () => {
  const base = {
    data_available: true,
    unavailable_reason: null,
    run_date: "2026-10-16",
    as_of: "2026-10-15",
    regime: "neutral",
    breadth_pct: 46,
    last_slot: "10:20",
    counts: {},
    items: [],
    events: [],
    masked: false,
  } satisfies Omit<StrategyWatchToday, "data_dates">;

  it("marks each post-close refresh done only once its data reaches the list's session", () => {
    const steps = pipelineSteps({
      ...base,
      data_dates: {
        price_eod: "2026-10-16",
        margin_short: "2026-10-15",
        institutional_flow: "2026-10-16",
      },
    });
    expect(steps.map((s) => s.key)).toEqual(["screen", "watch", "prices", "close", "flows"]);
    const prices = steps.find((s) => s.key === "prices");
    // Margin has not landed for today, so the 17:40 step is not done and says where it stops.
    expect(prices).toEqual({
      key: "prices",
      done: false,
      note: { kind: "dataThrough", date: "2026-10-15" },
    });
    expect(steps.find((s) => s.key === "flows")?.done).toBe(true);
  });

  it("shows where stale data stops when there is no list at all", () => {
    const steps = pipelineSteps({
      ...base,
      data_available: false,
      run_date: null,
      as_of: null,
      last_slot: null,
      data_dates: { price_eod: "2026-09-24", margin_short: "2026-09-24", institutional_flow: null },
    });
    expect(steps.every((s) => !s.done)).toBe(true);
    expect(steps.find((s) => s.key === "prices")?.note).toEqual({
      kind: "dataThrough",
      date: "2026-09-24",
    });
    expect(steps.find((s) => s.key === "flows")?.note).toEqual({ kind: "noData" });
  });
});
