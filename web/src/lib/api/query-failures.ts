/**
 * App-wide "a read really failed" signal, raised by the TanStack Query cache.
 *
 * Many panels render a calm empty state when their query has no data, which on its own makes a
 * failed request look like "no data". The shell listens here and says, once, that something did
 * not load. Only genuine failures count: a server error (5xx) or no response at all. Statuses
 * with their own handling are ignored: 401 (session refresh), 403 (entitlement), 404 (a missing
 * symbol is an answer), 429 (the rate-limit notice).
 */

type Listener = () => void;
const listeners = new Set<Listener>();

export function onQueryFailed(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function isGenuineFailure(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  if (typeof status !== "number" || status === 0) {
    return true; // network error, timeout, CORS: no response at all
  }
  return status >= 500;
}

export function reportQueryFailure(error: unknown): void {
  if (!isGenuineFailure(error)) return;
  for (const listener of listeners) listener();
}
