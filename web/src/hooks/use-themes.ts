"use client";

import { useQuery } from "@tanstack/react-query";
import { getThemeMembers, getThemes } from "@/lib/api/theme";
import type { ThemeKind } from "@/types/theme";

export function useThemes(kind?: ThemeKind) {
  return useQuery({
    queryKey: ["themes", kind ?? "all"],
    queryFn: () => getThemes(kind),
    staleTime: 5 * 60 * 1000,
  });
}

export function useThemeMembers(themeKey: string | null) {
  return useQuery({
    queryKey: ["theme-members", themeKey],
    queryFn: () => getThemeMembers(themeKey ?? ""),
    enabled: themeKey !== null,
  });
}
