import { describe, expect, it, vi } from "vitest";
import { ALL_MARKETS, createRefreshScheduler, isMarketDataQuery } from "@/lib/data-update-refresh";

const tw = new Set(["TW"]);

describe("isMarketDataQuery", () => {
  it("matches reads whatever position the market sits in", () => {
    expect(isMarketDataQuery(["stock_day_trading", "2330", "TW"], tw)).toBe(true);
    expect(isMarketDataQuery(["price_history", "TW", "2330", 90], tw)).toBe(true);
    expect(isMarketDataQuery(["etf_list", "TW"], tw)).toBe(true);
    expect(isMarketDataQuery(["dividend_calendar", "TW", 2026, 10], tw)).toBe(true);
  });

  it("leaves the other market's reads alone", () => {
    expect(isMarketDataQuery(["stock_day_trading", "AAPL", "US"], tw)).toBe(false);
    expect(isMarketDataQuery(["etf_list", "US"], tw)).toBe(false);
  });

  it("reads the market from a name prefix too", () => {
    expect(isMarketDataQuery(["tw_market_rankings", "dealer_streak", 50], tw)).toBe(true);
    expect(isMarketDataQuery(["us_market_rankings", "gainers", 50], tw)).toBe(false);
    expect(isMarketDataQuery(["us_market_rankings", "gainers", 50], new Set(["US"]))).toBe(true);
  });

  it("refreshes a read that names no market, on any signal", () => {
    expect(isMarketDataQuery(["market_index_strip"], tw)).toBe(true);
    expect(isMarketDataQuery(["theme_tier_heatmap", "ai"], new Set(["US"]))).toBe(true);
  });

  it("never touches account state or live feeds", () => {
    for (const name of [
      "settings",
      "me",
      "watchlists",
      "thread",
      "threads",
      "notifications",
      "stock_live_quote",
      "tick_snapshot",
      "news_feed",
    ]) {
      expect(isMarketDataQuery([name, "TW"], tw)).toBe(false);
      expect(isMarketDataQuery([name], new Set([ALL_MARKETS]))).toBe(false);
    }
  });

  it("refreshes every market's reads for a catch-up", () => {
    const all = new Set([ALL_MARKETS]);
    expect(isMarketDataQuery(["stock_day_trading", "AAPL", "US"], all)).toBe(true);
    expect(isMarketDataQuery(["etf_list", "TW"], all)).toBe(true);
  });

  it("ignores a key without a string name", () => {
    expect(isMarketDataQuery([], tw)).toBe(false);
    expect(isMarketDataQuery([42, "TW"], tw)).toBe(false);
  });
});

function harness(options: { hidden?: boolean; random?: number } = {}) {
  let hidden = options.hidden ?? false;
  let nextHandle = 1;
  const timers = new Map<number, { fn: () => void; ms: number }>();
  const run = vi.fn<(markets: ReadonlySet<string>) => void>();
  const scheduler = createRefreshScheduler({
    run,
    windowMs: 2_000,
    jitterMs: 20_000,
    isHidden: () => hidden,
    random: () => options.random ?? 0.5,
    setTimer: (fn, ms) => {
      const handle = nextHandle++;
      timers.set(handle, { fn, ms });
      return handle;
    },
    clearTimer: (handle) => {
      timers.delete(handle as number);
    },
  });
  const fireAll = (): void => {
    for (const [handle, timer] of [...timers]) {
      timers.delete(handle);
      timer.fn();
    }
  };
  return {
    scheduler,
    run,
    timers,
    fireAll,
    setHidden: (value: boolean) => {
      hidden = value;
    },
  };
}

describe("createRefreshScheduler", () => {
  it("merges a burst of signals into one refresh after the window plus a random spread", () => {
    const h = harness({ random: 0.25 });

    h.scheduler.add("TW");
    h.scheduler.add("TW");
    h.scheduler.add("US");
    h.scheduler.add("TW");

    expect(h.timers.size).toBe(1); // one timer, not one per signal
    expect([...h.timers.values()][0]?.ms).toBe(2_000 + 0.25 * 20_000);
    h.fireAll();
    expect(h.run).toHaveBeenCalledTimes(1);
    expect([...(h.run.mock.calls[0]?.[0] ?? [])].sort()).toEqual(["TW", "US"]);
  });

  it("spreads tabs: different random draws give different delays", () => {
    const early = harness({ random: 0 });
    const late = harness({ random: 0.99 });

    early.scheduler.add("TW");
    late.scheduler.add("TW");

    expect([...early.timers.values()][0]?.ms).toBe(2_000);
    expect([...late.timers.values()][0]?.ms).toBeGreaterThan(21_000);
  });

  it("starts a new window after a refresh", () => {
    const h = harness();
    h.scheduler.add("TW");
    h.fireAll();

    h.scheduler.add("TW");
    h.fireAll();

    expect(h.run).toHaveBeenCalledTimes(2);
  });

  it("holds the refresh while the tab is hidden and runs it once shown", () => {
    const h = harness({ hidden: true });
    h.scheduler.add("TW");
    h.fireAll();
    expect(h.run).not.toHaveBeenCalled(); // hidden: nothing refetched

    h.scheduler.flushIfVisible();
    expect(h.timers.size).toBe(0); // still hidden: stays held

    h.setHidden(false);
    h.scheduler.flushIfVisible();
    h.fireAll();
    expect(h.run).toHaveBeenCalledTimes(1);
    expect([...(h.run.mock.calls[0]?.[0] ?? [])]).toEqual(["TW"]);
  });

  it("does nothing when shown with nothing pending", () => {
    const h = harness();

    h.scheduler.flushIfVisible();

    expect(h.timers.size).toBe(0);
    expect(h.run).not.toHaveBeenCalled();
  });

  it("drops what is pending when cancelled", () => {
    const h = harness();
    h.scheduler.add("TW");

    h.scheduler.cancel();
    h.fireAll();

    expect(h.run).not.toHaveBeenCalled();
  });
});
