"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

/** App-wide TanStack Query client (web/CLAUDE.md §3: client-interactive reads live here — thread
 * list, persisted thread history). One instance per browser session, created lazily so it isn't
 * shared across SSR requests. `staleTime` is conservative: threads are cheap cold reads refetched
 * on turn-end invalidation, not a high-frequency feed. */
export function QueryProvider({ children }: { children: ReactNode }): ReactNode {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            gcTime: 5 * 60_000,
            // One quick retry for a blip; a 429 was already waited out and retried at the fetch
            // layer, so retrying it again here would only hit the limiter harder.
            retry: (failureCount, error) =>
              failureCount < 1 && (error as { status?: number }).status !== 429,
            refetchOnWindowFocus: false,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
