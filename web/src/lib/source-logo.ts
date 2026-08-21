/**
 * Resolves a web-search result's URL to a source-site logo image: a pre-downloaded local file
 * under `public/sources/` for the finance/news domains we see regularly (fast, no third-party
 * request, works offline), falling back to Google's public favicon service for any other domain
 * `web_search` happens to return — native web search can surface an arbitrary site, so a fixed
 * local set alone would leave most sources with no icon at all.
 */

const KNOWN_DOMAINS: readonly string[] = [
  "cnyes.com",
  "udn.com",
  "technews.tw",
  "ithome.com.tw",
  "blocktempo.com",
  "moneydj.com",
  "wantgoo.com",
  "businesstoday.com.tw",
  "ctee.com.tw",
  "chinatimes.com",
  "ettoday.net",
  "cmoney.tw",
  "tw.stock.yahoo.com",
  "wsj.com",
  "bloomberg.com",
  "reuters.com",
  "cnbc.com",
  "ft.com",
  "finance.yahoo.com",
];

function domainOf(url: string): string | null {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

/** The registrable domain a known local logo was downloaded for, or the hostname itself if it
 * isn't in `KNOWN_DOMAINS` — matches on a suffix so a subdomain (e.g. `news.cnyes.com`) still
 * resolves to `cnyes.com`'s logo. */
function matchKnownDomain(hostname: string): string | null {
  return KNOWN_DOMAINS.find((d) => hostname === d || hostname.endsWith(`.${d}`)) ?? null;
}

/** Returns a local `/sources/*.png` path for a known domain, or a live favicon-service URL as a
 * fallback for everything else. Never throws — an unparseable URL just gets `null`, and the
 * caller (`WebSearchSources`) renders the generic globe glyph in that case, exactly like before
 * this feature existed. */
export function sourceLogoUrl(url: string): string | null {
  const hostname = domainOf(url);
  if (!hostname) return null;
  const known = matchKnownDomain(hostname);
  if (known) {
    return `/sources/${known.replace(/\./g, "_")}.png`;
  }
  return `https://www.google.com/s2/favicons?domain=${hostname}&sz=64`;
}
