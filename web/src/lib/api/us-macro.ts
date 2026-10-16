/**
 * US macro statistics API client — mirrors the `/macro/us/*` routes (public, anonymous OK).
 * Reuses `market.ts`'s request seam so errors and 401 handling stay in one place.
 */

import { requestJson } from "@/lib/api/market";
import {
  type MacroUnits,
  type RevisionSeriesId,
  type UsEconomicReleasesResponse,
  type UsMacroDashboardResponse,
  type UsMacroSeriesResponse,
  type UsSeriesRevisionsResponse,
  usEconomicReleasesResponseSchema,
  usMacroDashboardResponseSchema,
  usMacroSeriesResponseSchema,
  usSeriesRevisionsResponseSchema,
} from "@/types/market/us-macro";

export async function getUsMacroDashboard(): Promise<UsMacroDashboardResponse> {
  const raw = await requestJson("/macro/us/dashboard");
  return usMacroDashboardResponseSchema.parse(raw);
}

export type UsMacroSeriesParams = {
  readonly seriesId: string;
  readonly units?: MacroUnits | undefined;
  readonly startDate?: string | undefined;
  readonly endDate?: string | undefined;
  readonly limit?: number | undefined;
};

export async function getUsMacroSeries(
  params: UsMacroSeriesParams,
): Promise<UsMacroSeriesResponse> {
  const search = new URLSearchParams({ series_id: params.seriesId });
  if (params.units) search.set("units", params.units);
  if (params.startDate) search.set("start_date", params.startDate);
  if (params.endDate) search.set("end_date", params.endDate);
  if (params.limit !== undefined) search.set("limit", String(params.limit));
  const raw = await requestJson(`/macro/us/series?${search.toString()}`);
  return usMacroSeriesResponseSchema.parse(raw);
}

export type UsMacroReleasesParams = {
  readonly fromDate?: string | undefined;
  readonly toDate?: string | undefined;
};

/** No arguments = the backend's default window (recent past + upcoming). */
export async function getUsEconomicReleases(
  params: UsMacroReleasesParams = {},
): Promise<UsEconomicReleasesResponse> {
  const search = new URLSearchParams();
  if (params.fromDate) search.set("from_date", params.fromDate);
  if (params.toDate) search.set("to_date", params.toDate);
  const suffix = search.toString();
  const raw = await requestJson(suffix ? `/macro/us/releases?${suffix}` : "/macro/us/releases");
  return usEconomicReleasesResponseSchema.parse(raw);
}

export async function getUsSeriesRevisions(
  seriesId: RevisionSeriesId,
): Promise<UsSeriesRevisionsResponse> {
  const raw = await requestJson(
    `/macro/us/revisions?${new URLSearchParams({ series_id: seriesId })}`,
  );
  return usSeriesRevisionsResponseSchema.parse(raw);
}
