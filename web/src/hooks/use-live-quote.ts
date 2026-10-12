"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import { subscribeQuoteStream } from "@/lib/api/quote-stream";
import type { LivePriceTick } from "@/types/live-quote";
import type { Market } from "@/types/settings";

export type LiveQuoteSnapshot = {
  readonly tick: LivePriceTick | null;
  readonly marketOpen: boolean;
  readonly asOf: string | null;
};

const EMPTY_SNAPSHOT: LiveQuoteSnapshot = { tick: null, marketOpen: false, asOf: null };

/** Subscribes to `GET /quote/stream` for one `(symbol, market)` and exposes the latest
 * tick via `useSyncExternalStore` — per web/OVERVIEW.md §5 ("continuous values use motion values /
 * refs — never `useState` per tick"), this hook holds no `useState` of its own; each incoming SSE
 * frame is coalesced onto the next animation frame (rAF-batched) before notifying subscribers, so
 * a burst of frames within one frame produces at most one re-render, matching the "rAF-batched SSE
 * ticks" contract the doc specifies for the watchlist/live layer. Only ever active for
 * `market === "US"` — TW/ETF still read price via `getPriceHistory` (no ingested US EOD partition
 * exists yet, which is the whole reason this hook exists). */
export function useLiveQuote(symbol: string, market: Market): LiveQuoteSnapshot {
  const enabled = market === "US";
  const snapshotRef = useRef<LiveQuoteSnapshot>(EMPTY_SNAPSHOT);
  const listenersRef = useRef(new Set<() => void>());
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    snapshotRef.current = EMPTY_SNAPSHOT;
    for (const listener of listenersRef.current) listener();
    if (!enabled) return;

    const handle = subscribeQuoteStream([symbol], market, (frame) => {
      if (frame.symbol !== symbol) return;
      snapshotRef.current = {
        tick: frame.quote.tick ?? null,
        marketOpen: frame.market_open,
        asOf: frame.as_of ?? null,
      };
      if (rafRef.current !== null) return;
      rafRef.current = requestAnimationFrame(() => {
        rafRef.current = null;
        for (const listener of listenersRef.current) listener();
      });
    });

    return () => {
      handle.close();
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [symbol, market, enabled]);

  return useSyncExternalStore(
    (listener) => {
      listenersRef.current.add(listener);
      return () => listenersRef.current.delete(listener);
    },
    () => snapshotRef.current,
    () => EMPTY_SNAPSHOT,
  );
}
