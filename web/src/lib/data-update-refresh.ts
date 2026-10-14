/**
 * What the `data.updated` push does to the query cache.
 *
 * Ingest tells every open tab that a market's data landed. This decides which cached reads that
 * touches and when they are refreshed, so the push is useful and cannot become a load spike.
 *
 * - **Which reads.** Query keys in this app have no common `[capability, market]` shape: the market
 *   sits second (`["stock_day_trading", symbol, market]`), first (`["price_history", market, ...]`)
 *   or nowhere. So a read is refreshed when its key names the signalled market (or names none),
 *   unless it is account or live state: settings, threads, notifications and the like are not
 *   ingest data, and live quotes have their own cadence. New market reads are refreshed without
 *   being listed; a new account-state key only costs an extra refetch if it is not listed.
 * - **Only reads on screen** are refetched (`refetchType: "active"`); the rest are marked stale and
 *   refetch when next shown.
 * - **Coalesced and spread out.** A backfill or the nightly run sends many signals in a row, and
 *   every open tab receives each at the same instant. Signals are merged over a short window and
 *   then the refresh is delayed by a random amount, so thousands of tabs do not refetch together.
 * - **Not while hidden.** A background tab waits until it is shown again.
 */

/** Keys that are not ingest-backed market data: account state, and feeds with their own cadence. */
const NOT_MARKET_DATA: ReadonlySet<string> = new Set([
  "settings",
  "me",
  "watchlists",
  "thread",
  "threads",
  "notifications",
  "byok-keys",
  "symbol_search",
  "ptt_stock_search",
  "ptt_board",
  "news_feed",
  "stock_finmind_news",
  "sec_filings",
  "stock_live_quote",
  "live_price_quote",
  "stock_price_tick",
  "stock_intraday_kbar",
  "tick_snapshot",
]);

const MARKETS: ReadonlySet<string> = new Set(["TW", "US"]);

/** Every market, for a refresh that cannot say which one it is for (after a reconnect). */
export const ALL_MARKETS = "*";

/** The market a key names: in its parts (`["etf_list", "TW"]`), or in its name's prefix
 * (`tw_market_rankings`, `us_market_rankings`). `null` when it names none. */
function marketsNamedBy(queryKey: readonly unknown[], name: string): string[] {
  const named = queryKey.filter(
    (part): part is string => typeof part === "string" && MARKETS.has(part),
  );
  const prefix = name.slice(0, 3).toUpperCase();
  if (prefix === "TW_" || prefix === "US_") {
    named.push(prefix.slice(0, 2));
  }
  return named;
}

export function isMarketDataQuery(
  queryKey: readonly unknown[],
  markets: ReadonlySet<string>,
): boolean {
  const name = queryKey[0];
  if (typeof name !== "string" || NOT_MARKET_DATA.has(name)) {
    return false;
  }
  if (markets.has(ALL_MARKETS)) {
    return true;
  }
  const named = marketsNamedBy(queryKey, name);
  if (named.length === 0) {
    // Names no market (an index strip, a theme heatmap): refresh on any signal, it may depend on
    // either.
    return true;
  }
  return named.some((market) => markets.has(market));
}

export type RefreshScheduler = {
  /** Note that `market` (or `ALL_MARKETS`) has new data. */
  add: (market: string) => void;
  /** Run a refresh that was held back because the tab was hidden. */
  flushIfVisible: () => void;
  cancel: () => void;
};

export type RefreshSchedulerOptions = {
  /** Refresh the reads for these markets. */
  run: (markets: ReadonlySet<string>) => void;
  /** Signals within this long of the first are merged into one refresh. */
  windowMs?: number;
  /** Then wait a random 0..jitterMs before refreshing. */
  jitterMs?: number;
  isHidden?: () => boolean;
  random?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
};

export function createRefreshScheduler(options: RefreshSchedulerOptions): RefreshScheduler {
  const {
    run,
    windowMs = 2_000,
    jitterMs = 20_000,
    isHidden = () => typeof document !== "undefined" && document.hidden,
    random = Math.random,
    setTimer = (fn, ms) => setTimeout(fn, ms),
    clearTimer = (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  } = options;

  let pending = new Set<string>();
  let timer: unknown = null;

  const fire = (): void => {
    timer = null;
    if (pending.size === 0) {
      return;
    }
    if (isHidden()) {
      return; // kept in `pending`; `flushIfVisible` runs it when the tab is shown
    }
    const markets = pending;
    pending = new Set();
    run(markets);
  };

  return {
    add: (market) => {
      pending.add(market);
      if (timer === null) {
        timer = setTimer(fire, windowMs + random() * jitterMs);
      }
    },
    flushIfVisible: () => {
      if (pending.size === 0 || isHidden()) {
        return;
      }
      if (timer !== null) {
        clearTimer(timer);
      }
      // Shown again after a while: spread this too, so tabs shown together do not refetch together.
      timer = setTimer(fire, random() * jitterMs);
    },
    cancel: () => {
      if (timer !== null) {
        clearTimer(timer);
      }
      timer = null;
      pending = new Set();
    },
  };
}
