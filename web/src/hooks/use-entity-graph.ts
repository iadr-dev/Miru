"use client";

import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { ENTITY_GRAPH_LIMIT, getEntityGraph } from "@/lib/api/entity-graph";
import { queryKeys } from "@/lib/api/query-keys";
import { FRESHNESS } from "@/lib/freshness";
import type { EntityGraphResponse } from "@/types/entity-graph";
import type { Market } from "@/types/settings";

export function useEntityGraph(
  symbol: string,
  market: Market,
): UseQueryResult<EntityGraphResponse> {
  return useQuery({
    queryKey: queryKeys.entityGraph(market, symbol, 1, ENTITY_GRAPH_LIMIT),
    queryFn: () => getEntityGraph(symbol, market, 1, ENTITY_GRAPH_LIMIT),
    staleTime: FRESHNESS.reference,
  });
}
