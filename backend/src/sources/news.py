"""News adapter — the near-real-time news freshness layer.

Aggregates two keyless/near-keyless sources into one normalized `NewsItem` list, market-branched:

- **TW** (`market=TW`, the default): **Financial RSS** — 鉅亨網 cnyes (台股/頭條), 經濟日報 udn
  money, TechNews, iThome, BlockTempo, Yahoo股市 (all genuinely TW-only outlets, no auth, always
  fetched whole — not query-filterable upstream) **plus** Finnhub's **company-news** endpoint
  scoped by `query` (a real ticker symbol expected here).
- **Non-TW** (`market != TW`): the RSS half is skipped entirely (those 7 feeds are Taiwan
  financial/tech outlets with no non-TW equivalent — mixing them into a "US news" feed produced
  visibly TW-only headlines, confirmed live) and Finnhub's **general market news** category
  (`/news?category=general`) is used instead of company-news: company-news requires a real
  ticker as its `symbol` param, so passing a free-text phrase like `"US stock market"` there (the
  original bug) silently returned zero Finnhub items every time — `category=general` takes no
  symbol/keyword at all and returns broad market news, which is what a whole-market news panel
  actually wants.

GDELT Doc 2.0 (keyless global news search) was removed 2026-09 after live verification found its
public endpoint persistently rate-limiting (HTTP 429) regardless of request spacing — see
`fetch_news`'s own docstring before re-adding it.

Third-party content: every `NewsItem` is cited by `link`/`source`, never restated as our own
analysis or a trading signal — the non-trading guardrail is the backstop, but this adapter never
emits raw HTML/XML downstream, only the normalized model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import struct_time
from typing import cast

import feedparser
import httpx
import structlog

from config.settings import Settings, get_settings, reveal
from schemas.capabilities import NewsItem
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.tls import build_async_client

logger = structlog.get_logger(__name__)

_CAVEAT = (
    "Third-party news/RSS content — near-real-time but unverified aggregation, not our own "
    "reporting or analysis; always cite the original source, never restate as a trading signal."
)

# Politeness: this adapter is user-driven (tool call), never a background poller — see
# governance's user-driven-only rule. A caller/scheduler must not invoke `fetch_news` more
# often than this per feed; the constant documents the floor, enforcement is the caller's job
# (a coarse scheduler refresh, not this adapter, owns any periodic cadence).
MIN_SECONDS_BETWEEN_REQUESTS = 30.0

_FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"
# Finnhub's whole-market news category (no symbol/keyword param) — see the module docstring for
# why this, not company-news, is the right endpoint for a non-TW market's general news panel.
_FINNHUB_MARKET_NEWS_URL = "https://finnhub.io/api/v1/news"
_FINNHUB_MARKET_NEWS_CATEGORY = "general"

# The two cast() target types every raw JSON list/dict-of-unknowns value below uses.
type _UntypedList = list[object]
type _UntypedDict = dict[str, object]


@dataclass(frozen=True)
class _RssFeed:
    source: str
    url: str


# Real, publicly documented feed endpoints (independently verified against the live outlets —
# not all of these are cross-referenced in this project's internal source inventory).
_RSS_FEEDS: tuple[_RssFeed, ...] = (
    _RssFeed(source="cnyes_tw_stock", url="https://news.cnyes.com/rss/v1/news/category/tw_stock"),
    _RssFeed(source="cnyes_headline", url="https://news.cnyes.com/rss/v1/news/category/headline"),
    _RssFeed(source="udn_money", url="https://money.udn.com/rssfeed/news/1001/5591?ch=money"),
    _RssFeed(source="technews", url="https://technews.tw/feed/"),
    _RssFeed(source="ithome", url="https://www.ithome.com.tw/rss"),
    _RssFeed(source="blocktempo", url="https://www.blocktempo.com/feed/"),
    _RssFeed(source="yahoo_tw_stock", url="https://tw.stock.yahoo.com/rss?category=news"),
)


def _parse_entry_time(entry: dict[str, object]) -> datetime:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if isinstance(parsed, struct_time):
        return datetime(*parsed[:6], tzinfo=UTC)
    published = entry.get("published") or entry.get("updated")
    if isinstance(published, str):
        try:
            dt = parsedate_to_datetime(published)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except (TypeError, ValueError):
            pass
    return datetime.now(UTC)


def _parse_rss_body(source: str, body: bytes) -> list[NewsItem]:
    # feedparser ships no type stubs — isolate the untyped 3rd-party edge to this one
    # function; every value pulled out is immediately coerced to a concrete type before
    # crossing into the typed `NewsItem` model.
    parsed = cast("object", feedparser.parse(body))
    raw_entries: object = getattr(parsed, "entries", [])
    entries = cast(_UntypedList, raw_entries) if isinstance(raw_entries, list) else []
    items: list[NewsItem] = []
    for raw_entry in entries:
        if not isinstance(raw_entry, dict):
            continue
        entry = cast(_UntypedDict, raw_entry)
        title = str(entry.get("title", "")).strip()
        link = str(entry.get("link", "")).strip()
        if not title or not link:
            continue
        summary_raw = entry.get("summary")
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=source,
                published_at=_parse_entry_time(entry),
                summary=str(summary_raw).strip() if summary_raw else None,
            )
        )
    return items


def _parse_finnhub_body(raw: object) -> list[NewsItem]:
    if not isinstance(raw, list):
        return []
    items: list[NewsItem] = []
    for raw_item in cast(_UntypedList, raw):
        if not isinstance(raw_item, dict):
            continue
        item = cast(_UntypedDict, raw_item)
        title = str(item.get("headline", "")).strip()
        link = str(item.get("url", "")).strip()
        if not title or not link:
            continue
        ts = item.get("datetime")
        published = (
            datetime.fromtimestamp(float(cast("float", ts)), tz=UTC)
            if isinstance(ts, (int, float))
            else datetime.now(UTC)
        )
        summary = item.get("summary")
        items.append(
            NewsItem(
                title=title,
                link=link,
                source=f"finnhub:{item.get('source', 'unknown')}",
                published_at=published,
                summary=str(summary).strip() if summary else None,
            )
        )
    return items


class NewsAdapter:
    """The `NEWS` source adapter — RSS + Finnhub, merged into one `NewsItem` list."""

    source_id = "NEWS"
    timing_class = TimingClass.REFERENCE

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        rss_feeds: tuple[_RssFeed, ...] = _RSS_FEEDS,
    ) -> None:
        self._settings = settings or get_settings()
        self._rss_feeds = rss_feeds

    def supports(self) -> set[tuple[Capability, Market]]:
        return {(Capability.NEWS, market) for market in Market}

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        items = raw.get("items")
        if not isinstance(items, list):
            raise ValueError("News response missing 'items' list")
        return raw

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        **params: object,
    ) -> SourceResult:
        if cap is not Capability.NEWS:
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"NEWS does not serve capability {cap.value}",
                ),
            )
        query = str(params.get("query") or sym.id)
        return await self.fetch_news(query=query, market=sym.market, tls_policy=tls_policy)

    async def fetch_news(
        self,
        *,
        query: str,
        market: Market = Market.TW,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        """Aggregate RSS + (optional) Finnhub for `query`, market-branched (see module docstring).
        `market=TW` (the default, unchanged behavior): RSS feeds fetched whole (not query-
        filterable) + Finnhub company-news scoped by `query`. Any other market: RSS skipped
        (TW-only outlets), Finnhub general-market-news category used instead (no query/symbol
        param — `query` is simply unused on this branch). GDELT Doc 2.0 was dropped from this
        aggregator (2026-09) after live verification found its public endpoint persistently
        rate-limiting (HTTP 429) regardless of request spacing — not a transient blip, so it
        isn't worth the added failure surface; re-add only after confirming the upstream quota
        issue has actually cleared."""
        items: list[NewsItem] = []
        errors: list[str] = []
        is_tw = market is Market.TW

        if is_tw:
            await self._collect_rss(items, errors, tls_policy)
        await self._collect_finnhub(items, errors, query, is_tw, tls_policy)

        items.sort(key=lambda item: item.published_at, reverse=True)
        return _news_result(items, errors)

    async def _collect_rss(
        self, items: list[NewsItem], errors: list[str], tls_policy: TLSPolicy
    ) -> None:
        for feed in self._rss_feeds:
            try:
                fetched = await self._fetch_rss(feed, tls_policy)
                items.extend(fetched)
            except httpx.HTTPError as exc:
                errors.append(f"{feed.source}: {type(exc).__name__}")
                logger.warning("news_rss_fetch_failed", source=feed.source, error=str(exc))

    async def _collect_finnhub(
        self,
        items: list[NewsItem],
        errors: list[str],
        query: str,
        is_tw: bool,
        tls_policy: TLSPolicy,
    ) -> None:
        finnhub_key = reveal(self._settings.finnhub_key)
        if not finnhub_key:
            return
        try:
            if is_tw:
                items.extend(await self._fetch_finnhub_company_news(query, finnhub_key, tls_policy))
            else:
                items.extend(await self._fetch_finnhub_market_news(finnhub_key, tls_policy))
        except httpx.HTTPError as exc:
            errors.append(f"finnhub: {type(exc).__name__}")
            logger.warning("news_finnhub_fetch_failed", error=str(exc))

    async def _fetch_rss(self, feed: _RssFeed, tls_policy: TLSPolicy) -> list[NewsItem]:
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(feed.url)
            response.raise_for_status()
            return _parse_rss_body(feed.source, response.content)

    async def _fetch_finnhub_company_news(
        self, query: str, token: str, tls_policy: TLSPolicy
    ) -> list[NewsItem]:
        today = datetime.now(UTC).date()
        params = {
            "symbol": query,
            "from": today.isoformat(),
            "to": today.isoformat(),
            "token": token,
        }
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(_FINNHUB_COMPANY_NEWS_URL, params=params)
            response.raise_for_status()
            raw: object = response.json()
            return _parse_finnhub_body(raw)

    async def _fetch_finnhub_market_news(self, token: str, tls_policy: TLSPolicy) -> list[NewsItem]:
        """Finnhub's whole-market news category — no symbol/keyword param, unlike company-news.
        The correct endpoint for a non-TW market's general news panel (see module docstring)."""
        params = {"category": _FINNHUB_MARKET_NEWS_CATEGORY, "token": token}
        async with build_async_client(tls_policy=tls_policy) as client:
            response = await client.get(_FINNHUB_MARKET_NEWS_URL, params=params)
            response.raise_for_status()
            raw: object = response.json()
            return _parse_finnhub_body(raw)


def _news_result(items: list[NewsItem], errors: list[str]) -> SourceResult:
    if not items and errors:
        return _result(
            payload={},
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail="; ".join(errors)),
        )
    status = HealthStatus.OK if items else HealthStatus.STALE
    detail = "ok" if items else "no items returned"
    if errors:
        detail = f"{detail} (partial failures: {'; '.join(errors)})"
    return _result(
        payload={"items": [item.model_dump(mode="json") for item in items]},
        health=SourceHealth(status=status, detail=detail),
    )


def _result(*, payload: dict[str, object], health: SourceHealth) -> SourceResult:
    return SourceResult(
        source_id=NewsAdapter.source_id,
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=_CAVEAT,
        payload=payload,
        health=health,
    )


# Exposed for tests that need to substitute a fixture feed list.
RssFeed = _RssFeed

__all__ = ["NewsAdapter", "RssFeed"]
