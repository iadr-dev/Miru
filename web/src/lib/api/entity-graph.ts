/**
 * Entity-graph API client — mirrors `GET /symbols/{symbol}/entity-graph`. Shares the read-only
 * `requestJson` seam with the other market-data clients.
 */
import { requestJson } from "@/lib/api/market";
import { type EntityGraphResponse, entityGraphResponseSchema } from "@/types/entity-graph";
import type { Market } from "@/types/settings";

export const ENTITY_GRAPH_LIMIT = 60;

export async function getEntityGraph(
  symbol: string,
  market: Market,
  minWeight = 1,
  limit = ENTITY_GRAPH_LIMIT,
): Promise<EntityGraphResponse> {
  const search = new URLSearchParams({
    market,
    min_weight: String(minWeight),
    limit: String(limit),
  });
  const raw = await requestJson(
    `/symbols/${encodeURIComponent(symbol)}/entity-graph?${search.toString()}`,
  );
  return entityGraphResponseSchema.parse(raw);
}
