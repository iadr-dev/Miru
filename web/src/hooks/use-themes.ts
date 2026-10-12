"use client";

import { type UseQueryResult, useQuery } from "@tanstack/react-query";
import { queryKeys } from "@/lib/api/query-keys";
import { getThemeMembers, getThemes } from "@/lib/api/theme";
import { FRESHNESS } from "@/lib/freshness";
import type { ThemeKind, ThemeMembersResponse, ThemesResponse } from "@/types/theme";

export function useThemes(kind?: ThemeKind): UseQueryResult<ThemesResponse> {
  return useQuery({
    queryKey: queryKeys.themes(kind ?? "all"),
    queryFn: () => getThemes(kind),
    staleTime: FRESHNESS.reference,
  });
}

export function useThemeMembers(themeKey: string | null): UseQueryResult<ThemeMembersResponse> {
  return useQuery({
    queryKey: queryKeys.themeMembers(themeKey ?? ""),
    queryFn: () => getThemeMembers(themeKey ?? ""),
    enabled: themeKey !== null,
  });
}
