/**
 * The pure half of the URL-state pattern `hooks/use-detail-tab.ts` introduced: apply a set of
 * `key -> value | null` updates to the current query string (`null` removes the key) and return
 * the href to `router.replace`. Kept pure so every URL-addressable view state (the `?tab=` of a
 * detail page, the `?sub=`/`?cmp=` of the stock-compare view) builds its href the same way.
 */
export function withSearchParams(
  pathname: string,
  current: URLSearchParams | string,
  updates: Readonly<Record<string, string | null>>,
): string {
  const params = new URLSearchParams(current.toString());
  for (const [key, value] of Object.entries(updates)) {
    if (value === null) {
      params.delete(key);
    } else {
      params.set(key, value);
    }
  }
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}
