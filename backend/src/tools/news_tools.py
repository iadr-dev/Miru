"""`get_news_feed` — the RSS/Finnhub news aggregator (`sources/news.py`'s `NewsAdapter`), wrapped
as its own tool rather than routed through `DataAccessRouter.fetch()`: `NEWS` has no
`(capability, market)` entry in `source_priority.yaml` (a query string isn't `(capability,
symbol)`-shaped), the same non-router-fetch pattern `web_search_tools.py` already establishes for
`WEB`. Reaches the adapter via `dataaccess/news.py`'s `NewsGateway` — the layering rule ("`tools/`
never imports `sources/` directly") still applies even to this non-router seam. Distinct from
`others_tools.py`'s `get_finmind_stock_news` (FinMind's own per-symbol news dataset) — the two are
separate real datasets, never merged into one row shape.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.news import NewsGateway
from schemas.enums import DataClass, Market
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args


class GetNewsFeedInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    query: str = Field(
        default="台股",
        min_length=1,
        description="Search phrase/ticker — only used for `market=TW`, where it scopes the "
        "Finnhub company-news call (RSS feeds are fetched whole regardless, not "
        "query-filterable upstream). Ignored for any other market — see `market`'s own "
        "description.",
    )
    market: Market = Field(
        default=Market.TW,
        description="TW (default): RSS (TW financial outlets) + Finnhub company-news scoped by "
        "`query`. Any other market: RSS is skipped (those feeds are TW-only outlets with no "
        "non-TW equivalent) and Finnhub's general market-news category is used instead of "
        "company-news (which requires a real ticker as `query`, not a free-text phrase).",
    )


class NewsFeedItemOut(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    link: str
    source: str
    published_at: str
    summary: str | None = None


class GetNewsFeedOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    # An empty result is ambiguous on its own: `unreachable` means the source could not be fetched
    # (an outage, NOT "nothing there"), `stale` means it answered with nothing.
    health_status: HealthStatus
    items: tuple[NewsFeedItemOut, ...] = Field(default_factory=tuple)


class GetNewsFeedTool(BaseTool):
    """`get_news_feed` — near-real-time news, market-branched (see `GetNewsFeedInput.market`'s
    own description). `market=TW`: financial RSS (cnyes/udn/technews/ithome/blocktempo/Yahoo股市)
    plus Finnhub company news scoped by `query`. Any other market: RSS skipped, Finnhub
    general-market-news category used instead. Finnhub half silently skipped (never a hard
    failure) when no token is configured (`Settings().finnhub_key`). Third-party content: every
    item is cited by `link`/`source`, never restated as analysis."""

    name = "get_news_feed"
    description = (
        "Get near-real-time news aggregated from financial RSS feeds and (if configured) "
        "Finnhub company news. Third-party content — cite, never restate as analysis or a "
        "trading signal."
    )
    input_model: type[BaseModel] = GetNewsFeedInput
    output_model: type[BaseModel] = GetNewsFeedOutput
    data_class = DataClass.PUBLIC

    def __init__(self, gateway: NewsGateway | None = None) -> None:
        self._gateway = gateway or NewsGateway()

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetNewsFeedInput, args)
        result = await self._gateway.fetch_news(
            query=typed.query, market=typed.market, tls_policy=ctx.tls_policy
        )
        raw_items = result.payload.get("items")
        items: tuple[NewsFeedItemOut, ...] = ()
        if isinstance(raw_items, list):
            typed_items = cast("list[dict[str, object]]", raw_items)
            items = tuple(NewsFeedItemOut.model_validate(item) for item in typed_items)
        return GetNewsFeedOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            items=items,
        )


def build_registration_table(gateway: NewsGateway | None = None) -> list[tuple[Tool, bool]]:
    return [(GetNewsFeedTool(gateway), False)]


__all__ = [
    "GetNewsFeedInput",
    "GetNewsFeedOutput",
    "GetNewsFeedTool",
    "NewsFeedItemOut",
    "build_registration_table",
]
