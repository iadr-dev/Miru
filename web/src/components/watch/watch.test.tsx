// @vitest-environment jsdom
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { pipelineSteps } from "@/components/watch/run-status";
import {
  minutesOf,
  nextCheck,
  positionOf,
  SESSION_SLOTS,
  taipeiClock,
} from "@/components/watch/timeline-math";
import {
  distanceToLevel,
  initialStrategy,
  keyLevel,
  previewItems,
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
    slots: [],
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

describe("timeline-math", () => {
  it("places the steps on the track in time order, with the session widest", () => {
    const at = (t: string): number => positionOf(minutesOf(t));
    expect(at("08:00")).toBeLessThan(at("09:00"));
    expect(at("13:30") - at("09:00")).toBeGreaterThan(at("20:10") - at("13:30"));
    expect(at("17:40")).toBeLessThan(at("18:00"));
    expect(at("06:00")).toBe(0);
    expect(at("23:00")).toBe(100);
  });

  it("has 28 checks from 09:00 to 13:30", () => {
    expect(SESSION_SLOTS).toHaveLength(28);
    expect(SESSION_SLOTS[0]).toBe("09:00");
    expect(SESSION_SLOTS.at(-1)).toBe("13:30");
  });

  it("counts down to the next check and stops after the session", () => {
    expect(nextCheck(minutesOf("10:24"))).toEqual({ slot: "10:30", inMinutes: 6 });
    expect(nextCheck(minutesOf("10:30"))).toEqual({ slot: "10:30", inMinutes: 0 });
    expect(nextCheck(minutesOf("08:15"))).toEqual({ slot: "09:00", inMinutes: 45 });
    expect(nextCheck(minutesOf("13:31"))).toBeNull();
  });

  it("reads the Taipei clock regardless of the machine's time zone", () => {
    expect(taipeiClock(new Date("2026-10-16T02:24:00Z"))).toEqual({
      date: "2026-10-16",
      minutes: 10 * 60 + 24,
    });
  });
});

describe("previewItems", () => {
  const rows = Array.from({ length: 12 }, (_, i) => ({ symbol: String(1000 + i) }));

  it("shows the first rows", () => {
    expect(previewItems(rows, null, 8).map((r) => r.symbol)).toEqual(
      rows.slice(0, 8).map((r) => r.symbol),
    );
  });

  it("keeps a stock picked from the full list visible in the last row", () => {
    const shown = previewItems(rows, "1010", 8).map((r) => r.symbol);
    expect(shown).toHaveLength(8);
    expect(shown.at(-1)).toBe("1010");
    expect(shown.slice(0, 7)).toEqual(rows.slice(0, 7).map((r) => r.symbol));
  });
});
