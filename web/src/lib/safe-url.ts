/**
 * Guards against rendering an untrusted `javascript:`/`data:`/etc. URI as an `href` — third-party
 * content (news items, PTT posts, web-search results) is data, never instructions
 * (web/CLAUDE.md §8). Returns the URL string unchanged if it parses as `http(s)`, `null`
 * otherwise; callers should skip the link (or render inert text) on `null`.
 */
export function safeHttpUrl(raw: string): string | null {
  try {
    const url = new URL(raw);
    return url.protocol === "https:" || url.protocol === "http:" ? raw : null;
  } catch {
    return null;
  }
}
