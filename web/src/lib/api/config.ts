/** Backend origin for the /api/v1 surface. The only piece of backend coupling that varies per
 * environment; everything else is derived from it. */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

export function apiUrl(path: string): string {
  const base = API_BASE.replace(/\/$/, "");
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${base}${suffix}`;
}
