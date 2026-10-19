/**
 * Pure maths + URL helpers behind the stock compare overlay (技術分析 > 比較): up to
 * `COMPARE_MAX_SYMBOLS` TW symbols drawn as % change since one shared base date, so a 2330 at
 * NT$1,000 and a 2454 at NT$90 read on the same scale. No React, no fetching: the panel feeds
 * in the close series it already has and renders what comes back.
 */
import type { ThemeMember } from "@/types/theme";

export const COMPARE_MAX_SYMBOLS = 5;

export const COMPARE_RANGES = ["1m", "3m", "6m", "1y"] as const;
export type CompareRange = (typeof COMPARE_RANGES)[number];
export const DEFAULT_COMPARE_RANGE: CompareRange = "3m";

/** Trailing trading-day bars requested per range (one more than the intervals so the first bar is
 * the base). Stays under the backend's 260-day price-history cap. */
export const COMPARE_RANGE_DAYS: Readonly<Record<CompareRange, number>> = {
  "1m": 22,
  "3m": 64,
  "6m": 127,
  "1y": 253,
};

// ---------------------------------------------------------------------------
// URL state — `?sub=compare&cmp=2330,2454`
// ---------------------------------------------------------------------------

export const TECHNICAL_SUBS = ["chart", "compare"] as const;
export type TechnicalSub = (typeof TECHNICAL_SUBS)[number];

export function parseTechnicalSub(raw: string | null): TechnicalSub {
  return raw === "compare" ? "compare" : "chart";
}

/** A TW stock or ETF code: 4-6 digits, optionally one trailing letter (00631L). */
const TW_SYMBOL = /^[0-9]{4,6}[A-Z]?$/;

export function isCompareSymbol(value: string): boolean {
  return TW_SYMBOL.test(value);
}

/** `?cmp=` -> an ordered, de-duplicated, validated list capped at the limit. `null` when the
 * parameter is absent or holds only unusable text (the caller then seeds its own default); `[]`
 * for an explicitly empty `?cmp=`. */
export function parseCompareSymbols(raw: string | null): string[] | null {
  if (raw === null) {
    return null;
  }
  if (raw.trim() === "") {
    return []; // an explicit empty list: the user removed every stock, do not re-seed it
  }
  const symbols: string[] = [];
  for (const part of raw.split(",")) {
    const code = part.trim().toUpperCase();
    if (isCompareSymbol(code) && !symbols.includes(code)) {
      symbols.push(code);
    }
    if (symbols.length === COMPARE_MAX_SYMBOLS) {
      break;
    }
  }
  return symbols.length > 0 ? symbols : null;
}

/** The `?cmp=` value for a list (empty string for an emptied list, which `parseCompareSymbols`
 * reads back as empty rather than as "absent"). */
export function serializeCompareSymbols(symbols: readonly string[]): string {
  return symbols.join(",");
}

export function canAddCompareSymbol(symbols: readonly string[], symbol: string): boolean {
  return (
    symbols.length < COMPARE_MAX_SYMBOLS && isCompareSymbol(symbol) && !symbols.includes(symbol)
  );
}

/** Append a symbol; the same list comes back when it is full, a duplicate or not a code. */
export function addCompareSymbol(symbols: readonly string[], symbol: string): readonly string[] {
  return canAddCompareSymbol(symbols, symbol) ? [...symbols, symbol] : symbols;
}

export function removeCompareSymbol(symbols: readonly string[], symbol: string): readonly string[] {
  return symbols.filter((code) => code !== symbol);
}

/** The compare view's deep link, e.g. from a watchlist group: the first symbols of a list. */
export function compareHref(symbols: readonly string[]): string | null {
  const codes = symbols.filter(isCompareSymbol).slice(0, COMPARE_MAX_SYMBOLS);
  const [first] = codes;
  if (first === undefined) {
    return null;
  }
  const query = new URLSearchParams({ tab: "technical", sub: "compare", cmp: codes.join(",") });
  return `/stock/${encodeURIComponent(first)}?${query.toString()}`;
}

/** Up to `count` stock peers for the default view: theme members other than the current symbol,
 * TW only, plain stocks (an ETF is not a peer), most confident first. */
export function pickPeers(
  members: readonly ThemeMember[],
  current: string,
  count: number,
): string[] {
  const peers: string[] = [];
  const ranked = members
    .filter((member) => member.market === "TW" && member.symbol !== current)
    .sort((a, b) => b.confidence - a.confidence);
  for (const member of ranked) {
    // A plain stock code is exactly four digits; ETFs (00xxxx), warrants and the like are not peers.
    if (/^[1-9][0-9]{3}$/.test(member.symbol) && !peers.includes(member.symbol)) {
      peers.push(member.symbol);
    }
    if (peers.length === count) {
      break;
    }
  }
  return peers;
}

// ---------------------------------------------------------------------------
// Rebasing
// ---------------------------------------------------------------------------

export type CloseBar = { readonly date: string; readonly close: number };
export type CompareInput = { readonly symbol: string; readonly bars: readonly CloseBar[] };

export type RebasedRow = {
  readonly date: string;
  /** % change since the base date per symbol; `null` where that symbol has no bar that day. */
  readonly pct: Readonly<Record<string, number | null>>;
};

export type CompareSeries = {
  readonly symbol: string;
  /** % change at the symbol's last bar in the window, `null` when it has no usable data. */
  readonly endPct: number | null;
  readonly endDate: string | null;
};

export type RebaseResult = {
  /** The shared date every line starts from (0%); `null` when nothing could be drawn. */
  readonly baseDate: string | null;
  readonly rows: readonly RebasedRow[];
  readonly series: readonly CompareSeries[];
  /** Symbols with no usable bars in the window: listed, never drawn, never invented. */
  readonly missing: readonly string[];
  /** Set when the base date is later than the range start because these symbols' history begins
   * there; every line was rebased from that common date instead. */
  readonly limitedBy: readonly string[];
};

function cleanBars(bars: readonly CloseBar[]): CloseBar[] {
  const byDate = new Map<string, number>();
  for (const bar of bars) {
    if (Number.isFinite(bar.close) && bar.close > 0) {
      byDate.set(bar.date, bar.close);
    }
  }
  return [...byDate.entries()]
    .map(([date, close]) => ({ date, close }))
    .sort((a, b) => a.date.localeCompare(b.date));
}

/** Rebase every symbol to 0% on one common base date: the latest of the symbols' first dates, so
 * all lines start together. A symbol with no bar exactly on that date (a halt) is based on its
 * last close before it. Days a symbol did not trade are `null` gaps, never filled in. */
export function rebaseToCommonStart(inputs: readonly CompareInput[]): RebaseResult {
  const cleaned = inputs.map((input) => ({ symbol: input.symbol, bars: cleanBars(input.bars) }));
  const usable = cleaned.filter((input) => input.bars.length > 0);
  const missing = cleaned.filter((input) => input.bars.length === 0).map((input) => input.symbol);
  const firstDates = usable.map((input) => input.bars[0]?.date ?? "");
  const baseDate = firstDates.reduce((latest, date) => (date > latest ? date : latest), "");
  if (usable.length === 0 || baseDate === "") {
    return {
      baseDate: null,
      rows: [],
      series: inputs.map((input) => ({ symbol: input.symbol, endPct: null, endDate: null })),
      missing,
      limitedBy: [],
    };
  }
  const earliest = firstDates.reduce((first, date) => (date < first ? date : first), baseDate);
  const limitedBy =
    baseDate > earliest
      ? usable.filter((input) => input.bars[0]?.date === baseDate).map((input) => input.symbol)
      : [];

  const baseCloseBySymbol = new Map<string, number>();
  const closeByDate = new Map<string, Map<string, number>>();
  const dates = new Set<string>();
  for (const { symbol, bars } of usable) {
    const closes = new Map<string, number>();
    let baseClose: number | undefined;
    for (const bar of bars) {
      if (bar.date <= baseDate) {
        baseClose = bar.close;
      }
      if (bar.date >= baseDate) {
        closes.set(bar.date, bar.close);
        dates.add(bar.date);
      }
    }
    if (baseClose !== undefined) {
      baseCloseBySymbol.set(symbol, baseClose);
    }
    closeByDate.set(symbol, closes);
  }

  const rows: RebasedRow[] = [...dates]
    .sort((a, b) => a.localeCompare(b))
    .map((date) => {
      const pct: Record<string, number | null> = {};
      for (const { symbol } of usable) {
        const close = closeByDate.get(symbol)?.get(date);
        const base = baseCloseBySymbol.get(symbol);
        pct[symbol] = close === undefined || base === undefined ? null : (close / base - 1) * 100;
      }
      return { date, pct };
    });

  const series: CompareSeries[] = inputs.map(({ symbol }) => {
    const closes = closeByDate.get(symbol);
    const base = baseCloseBySymbol.get(symbol);
    if (closes === undefined || base === undefined || closes.size === 0) {
      return { symbol, endPct: null, endDate: null };
    }
    const endDate = [...closes.keys()].sort((a, b) => a.localeCompare(b)).at(-1) ?? null;
    const endClose = endDate === null ? undefined : closes.get(endDate);
    return {
      symbol,
      endPct: endClose === undefined ? null : (endClose / base - 1) * 100,
      endDate,
    };
  });

  return { baseDate, rows, series, missing, limitedBy };
}
