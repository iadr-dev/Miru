/**
 * 策略盯盤 API client — `/strategy-watch/today` and `/strategy-watch/subscription`
 * (`backend/src/api/routers/scheduler/strategy_watch.py`). Requests go through the shared
 * `lib/api/http.ts` seam and every response is Zod-parsed at the boundary.
 */
import { ApiRequestError, createRequestJson, jsonHeaders } from "@/lib/api/http";
import {
  type StrategyKey,
  type StrategySubscription,
  type StrategySubscriptionInput,
  type StrategyWatchToday,
  strategySubscriptionOrNullSchema,
  strategySubscriptionSchema,
  strategyWatchTodaySchema,
} from "@/types/strategy-watch";

export class StrategyWatchApiError extends ApiRequestError {
  constructor(code: string, message: string, status: number, retryable: boolean) {
    super("StrategyWatchApiError", code, message, status, retryable);
  }
}

const requestJson = createRequestJson(
  (code, message, status, retryable) => new StrategyWatchApiError(code, message, status, retryable),
);

/** GET /strategy-watch/today — today's list (or the last trading day's) with each stock's levels
 * and where its conditions stand at the latest intraday check. */
export async function getStrategyWatchToday(
  options: { asOf?: string | undefined; strategy?: StrategyKey | undefined } = {},
): Promise<StrategyWatchToday> {
  const search = new URLSearchParams();
  if (options.asOf) search.set("as_of", options.asOf);
  if (options.strategy) search.set("strategy", options.strategy);
  const query = search.toString();
  const queryString = query ? `?${query}` : "";
  const raw = await requestJson(`/strategy-watch/today${queryString}`);
  return strategyWatchTodaySchema.parse(raw);
}

/** GET /strategy-watch/subscription — the user's subscription, or null before the first save. */
export async function getStrategySubscription(): Promise<StrategySubscription | null> {
  const raw = await requestJson("/strategy-watch/subscription");
  return strategySubscriptionOrNullSchema.parse(raw);
}

/** PUT /strategy-watch/subscription — create or replace it; a stale `expected_version` is 409. */
export async function putStrategySubscription(
  body: StrategySubscriptionInput,
): Promise<StrategySubscription> {
  const raw = await requestJson("/strategy-watch/subscription", {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  return strategySubscriptionSchema.parse(raw);
}
