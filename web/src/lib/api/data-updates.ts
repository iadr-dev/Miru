/**
 * The `GET /updates/stream` client (`backend/src/api/routers/context.py::updates_stream`).
 *
 * Unlike `agent-stream.ts`'s turn stream, this endpoint is a plain GET, so native `EventSource`
 * (browser-managed reconnect, `withCredentials` for the session cookie) is the right tool instead
 * of a hand-rolled fetch-body reader. `optional_user` on the backend route means an unauthenticated
 * caller still gets the feed (it carries no per-user data, only capability/market/as_of/scope).
 */
import { apiUrl } from "@/lib/api/config";
import { type DataUpdatedEvent, dataUpdatedEventSchema } from "@/types/data-updates";

export type DataUpdatesHandle = {
  close: () => void;
};

/** Opens the feed and invokes `onUpdate` for each valid `data.updated` frame. Malformed frames
 * (schema mismatch, unparsable JSON) are dropped silently — same parse-don't-trust posture as
 * `agent-stream.ts`'s `parseFrame`, never a thrown error mid-stream. Returns a handle whose
 * `close()` tears down the connection; call it from the consumer's cleanup. */
export function subscribeDataUpdates(
  onUpdate: (event: DataUpdatedEvent) => void,
): DataUpdatesHandle {
  const source = new EventSource(apiUrl("/updates/stream"), { withCredentials: true });
  source.addEventListener("data.updated", (raw) => {
    const messageEvent = raw as MessageEvent<string>;
    try {
      const parsed = dataUpdatedEventSchema.safeParse(JSON.parse(messageEvent.data));
      if (parsed.success) {
        onUpdate(parsed.data);
      }
    } catch {
      // malformed frame — dropped, never surfaced as a stream error
    }
  });
  return {
    close: () => source.close(),
  };
}
