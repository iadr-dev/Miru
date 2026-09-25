/**
 * The `GET /quote/stream` client (`backend/src/api/routers/context.py::quote_stream`) —
 * follows `data-updates.ts`'s exact shape (native `EventSource`, `withCredentials` for the
 * session cookie, Zod `safeParse` per frame, silent-drop on a malformed frame, a `close()`
 * handle) since this is the same "plain GET, browser-managed reconnect" case, just a different
 * event name (`quote`, not `data.updated`) and payload shape.
 */
import { apiUrl } from "@/lib/api/config";
import { type LiveQuoteFrame, liveQuoteFrameSchema } from "@/types/live-quote";
import type { Market } from "@/types/settings";

export type QuoteStreamHandle = {
  close: () => void;
};

/** Opens the feed for `symbols` (comma-joined) on `market` and invokes `onQuote` for each valid
 * `quote` frame. Returns a handle whose `close()` tears down the connection — call it from the
 * consumer's cleanup (e.g. a `useEffect` unmount), which also drops this connection's demand
 * refcount server-side (`QuoteFeed.stream`'s own `finally`). */
export function subscribeQuoteStream(
  symbols: readonly string[],
  market: Market,
  onQuote: (frame: LiveQuoteFrame) => void,
): QuoteStreamHandle {
  const search = new URLSearchParams({ symbols: symbols.join(","), market });
  const source = new EventSource(apiUrl(`/quote/stream?${search.toString()}`), {
    withCredentials: true,
  });
  source.addEventListener("quote", (raw) => {
    const messageEvent = raw as MessageEvent<string>;
    try {
      const parsed = liveQuoteFrameSchema.safeParse(JSON.parse(messageEvent.data));
      if (parsed.success) {
        onQuote(parsed.data);
      }
    } catch {
      // malformed frame — dropped, never surfaced as a stream error
    }
  });
  return {
    close: () => source.close(),
  };
}
