/**
 * Theme API client — mirrors `backend/src/api/routers/themes.py`'s `GET /themes` and
 * `GET /themes/{key}` routes. Reuses `lib/api/market.ts`'s `requestJson` seam (same error/auth
 * handling every other read-only market-data client already shares).
 */
import { requestJson } from "@/lib/api/market";
import type { ThemeKind } from "@/types/theme";
import { themeMembersResponseSchema, themesResponseSchema } from "@/types/theme";

export async function getThemes(kind?: ThemeKind) {
  const search = kind ? `?kind=${kind}` : "";
  const raw = await requestJson(`/themes${search}`);
  return themesResponseSchema.parse(raw);
}

/** A theme's full membership. One request shape for every caller (screener theme cards, the
 * stock page's 產業鏈 tiles), so they share one cache entry; `limit` covers the largest themes. */
export async function getThemeMembers(themeKey: string) {
  const search = new URLSearchParams({ limit: "1000" });
  const raw = await requestJson(`/themes/${encodeURIComponent(themeKey)}?${search.toString()}`);
  return themeMembersResponseSchema.parse(raw);
}
