"""`get_ptt_board` — the PTT board-index-listing scrape (`sources/ptt.py`'s `PttAdapter`),
wrapped as its own tool rather than routed through `DataAccessRouter.fetch()`: a board name is a
required per-call parameter, not a `(capability, market)` router entry — the same
non-router-fetch pattern `web_search_tools.py` establishes for `WEB`. Reaches the adapter via
`dataaccess/ptt.py`'s `PttGateway` — the layering rule ("`tools/` never imports `sources/`
directly") still applies even to this non-router seam. `PttAdapter.fetch_board` already enforces
the `stock_buzz`/`offtopic` board-tag split (`Beauty`/`Gossiping` -> offtopic,
`Stock`/`Tech_Job`/`Warrant` -> stock_buzz) and its own caveat per tag; this tool passes both
straight through, never re-classifying or mixing them.
"""

from __future__ import annotations

from typing import cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.ptt import PttGateway
from schemas.enums import DataClass
from tools.base import BaseTool, Tool, ToolContext, parse_args

# The only boards this tool will fetch — mirrors `sources/ptt.py`'s own `_BOARD_TAGS` keys
# exactly; kept as an explicit allow-list here (rather than accepting an arbitrary board string)
# so a bad/unexpected board name fails the input schema, not a live network 404.
PTT_BOARDS = ("Stock", "Tech_Job", "Warrant", "Beauty", "Gossiping")

# `get_ptt_stock_search` only ever searches the 3 usable-in-analysis boards — Beauty/Gossiping
# are off-topic by definition, never a place a stock-relevant post would legitimately live.
_STOCK_SEARCH_BOARDS = ["Stock", "Tech_Job", "Warrant"]


_MAX_LOOKBACK_DAYS = 14


class GetPttBoardInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    board: str = Field(
        default="Stock",
        description="Which PTT board's index listing to fetch: 'Stock' (股票版), 'Tech_Job' "
        "(科技業板), 'Warrant' (權證版) — all stock_buzz/usable-in-analysis; 'Beauty' (表特版), "
        "'Gossiping' (八卦版) — offtopic, excluded from any market claim.",
    )
    lookback_days: int = Field(
        default=1,
        ge=1,
        le=_MAX_LOOKBACK_DAYS,
        description="How many days back to walk the board's index (via its own 'older posts' "
        "nav link), 1 = today's index page only (the original single-page behavior). Capped at "
        f"{_MAX_LOOKBACK_DAYS} — PTT has no per-page date filter, so a wider window means more "
        "sequential page fetches, not a cheaper query.",
    )


class PttPostOut(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    board: str
    board_tag: str
    title: str
    post_tag: str | None = None
    author: str
    push_count: str
    link: str
    posted_date: str | None = None


class GetPttBoardOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    posts: tuple[PttPostOut, ...] = Field(default_factory=tuple)


class GetPttBoardTool(BaseTool):
    """`get_ptt_board` — one PTT board's current index-listing page (title/author/push-count/
    post-tag only; not post bodies/comments). Always caveated **unverified retail/industry
    chatter**, never a trading recommendation — see `sources/ptt.py`'s own module docstring for
    the stock_buzz/offtopic board split this tool passes straight through."""

    name = "get_ptt_board"
    description = (
        "Get one PTT board's current index-listing (title/author/push-count/post-tag per "
        "post). Unverified retail/industry chatter — buzz/attention signal only, never a "
        "trading recommendation. 'Beauty'/'Gossiping' boards are offtopic and must never be "
        "treated as market content."
    )
    input_model: type[BaseModel] = GetPttBoardInput
    output_model: type[BaseModel] = GetPttBoardOutput
    data_class = DataClass.PUBLIC

    def __init__(self, gateway: PttGateway | None = None) -> None:
        self._gateway = gateway or PttGateway()

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetPttBoardInput, args)
        result = await self._gateway.fetch_board(
            board=typed.board, tls_policy=ctx.tls_policy, lookback_days=typed.lookback_days
        )
        raw_posts = result.payload.get("posts")
        posts: tuple[PttPostOut, ...] = ()
        if isinstance(raw_posts, list):
            typed_posts = cast("list[dict[str, object]]", raw_posts)
            posts = tuple(PttPostOut.model_validate(post) for post in typed_posts)
        return GetPttBoardOutput(currentness_caveat=result.currentness_caveat, posts=posts)


class GetPttStockSearchInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '2330'.")
    company_name: str = Field(
        min_length=1,
        description="The company's display name (e.g. '台積電') — matched against post BODY "
        "text alongside the symbol id, since most real posts about a stock use its name, not "
        "its ticker.",
    )
    lookback_days: int = Field(
        default=7,
        ge=1,
        le=_MAX_LOOKBACK_DAYS,
        description="How many days of PTT history to search across Stock/Tech_Job/Warrant.",
    )


class GetPttStockSearchOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    posts: tuple[PttPostOut, ...] = Field(default_factory=tuple)


class GetPttStockSearchTool(BaseTool):
    """`get_ptt_stock_search` — best-effort per-symbol PTT search: fetches each candidate post's
    own page and matches the symbol id/company name against its BODY text (never just the
    title — most real posts about a stock don't name it in the title, confirmed empirically
    against live boards). Still explicitly best-effort: bounded by
    `sources.ptt.MAX_BODY_FETCHES_PER_SEARCH`, and a post whose body only refers to the stock
    indirectly (a nickname, an implied reference) is missed same as any keyword search would
    miss it. Always caveated **unverified retail/industry chatter**, never a trading
    recommendation — same governance posture as `get_ptt_board`."""

    name = "get_ptt_stock_search"
    description = (
        "Best-effort search of PTT's Stock/Tech_Job/Warrant boards for posts mentioning one "
        "stock (by symbol id or company name) in the post BODY, over the last N days. "
        "Unverified retail/industry chatter — buzz/attention signal only, never a trading "
        "recommendation. Not a complete list — a post that doesn't name the stock explicitly "
        "in its body is missed."
    )
    input_model: type[BaseModel] = GetPttStockSearchInput
    output_model: type[BaseModel] = GetPttStockSearchOutput
    data_class = DataClass.PUBLIC

    def __init__(self, gateway: PttGateway | None = None) -> None:
        self._gateway = gateway or PttGateway()

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetPttStockSearchInput, args)
        result = await self._gateway.search_boards(
            boards=_STOCK_SEARCH_BOARDS,
            needles=[typed.symbol, typed.company_name],
            tls_policy=ctx.tls_policy,
            lookback_days=typed.lookback_days,
        )
        raw_posts = result.payload.get("posts")
        posts: tuple[PttPostOut, ...] = ()
        if isinstance(raw_posts, list):
            typed_posts = cast("list[dict[str, object]]", raw_posts)
            posts = tuple(PttPostOut.model_validate(post) for post in typed_posts)
        return GetPttStockSearchOutput(currentness_caveat=result.currentness_caveat, posts=posts)


def build_registration_table(gateway: PttGateway | None = None) -> list[tuple[Tool, bool]]:
    return [
        (GetPttBoardTool(gateway), False),
        (GetPttStockSearchTool(gateway), False),
    ]


__all__ = [
    "PTT_BOARDS",
    "GetPttBoardInput",
    "GetPttBoardOutput",
    "GetPttBoardTool",
    "GetPttStockSearchInput",
    "GetPttStockSearchOutput",
    "GetPttStockSearchTool",
    "PttPostOut",
    "build_registration_table",
]
