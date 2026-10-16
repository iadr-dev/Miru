"use client";

import { keepPreviousData, type UseQueryResult, useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/query-keys";
import {
  getUsEconomicReleases,
  getUsMacroDashboard,
  getUsMacroSeries,
  getUsSeriesRevisions,
} from "@/lib/api/us-macro";
import { FRESHNESS } from "@/lib/freshness";
import type {
  MacroUnits,
  RevisionSeriesId,
  UsEconomicReleasesResponse,
  UsMacroDashboardResponse,
  UsMacroSeriesResponse,
  UsSeriesRevisionsResponse,
} from "@/types/market/us-macro";

/** Reads the US macro section's components share. The data refreshes once a day on the backend,
 * so every read uses the `slow` class and one cache entry per request. */

export function useUsMacroDashboard(): UseQueryResult<UsMacroDashboardResponse> {
  return useQuery({
    queryKey: queryKeys.usMacro.dashboard(),
    queryFn: getUsMacroDashboard,
    staleTime: FRESHNESS.slow,
  });
}

export function useUsMacroSeries(args: {
  readonly seriesId: string;
  readonly units: MacroUnits;
  readonly startDate: string;
  readonly limit: number;
}): UseQueryResult<UsMacroSeriesResponse> {
  const { seriesId, units, startDate, limit } = args;
  return useQuery({
    queryKey: queryKeys.usMacro.series(seriesId, units, startDate, limit),
    queryFn: () => getUsMacroSeries({ seriesId, units, startDate, limit }),
    staleTime: FRESHNESS.slow,
    // Switching units or range keeps the previous chart on screen until the next one lands.
    placeholderData: keepPreviousData,
  });
}

export function useUsEconomicReleases(): UseQueryResult<UsEconomicReleasesResponse> {
  return useQuery({
    queryKey: queryKeys.usMacro.releases(),
    queryFn: () => getUsEconomicReleases(),
    staleTime: FRESHNESS.slow,
  });
}

export function useUsSeriesRevisions(
  seriesId: RevisionSeriesId,
): UseQueryResult<UsSeriesRevisionsResponse> {
  return useQuery({
    queryKey: queryKeys.usMacro.revisions(seriesId),
    queryFn: () => getUsSeriesRevisions(seriesId),
    staleTime: FRESHNESS.slow,
  });
}
