"""Multi-factor score tools — the agent's read surface onto the daily-batch-computed
`stock_scores` cache (`analytics/scoring_store.py`).

Both tools are **cache-only, never live-compute**: `get_stock_score` returns the day's cached row
if present, or an honest "not yet computed for today" if not — it deliberately does not fall back
to a live per-symbol computation. The reason is structural, not just a shortcut: every factor here
is cross-sectional (`winsorized_percentile`/`zscore` rank a symbol against the *whole day's*
comparison universe), so answering for one symbol on demand would still require reading and
ranking against the whole universe's price/flow/fundamental history — exactly the expensive pass
the nightly batch already does once for everyone. A live per-symbol path would either duplicate
that whole-universe read (no cheaper than waiting for the batch) or compute against a stale/
partial universe (a silently different, non-comparable number) — both worse than a clear
"not yet available" until the next batch lands.

`get_score_leaderboard` is a pure sort-and-slice read over the same cache — the batch write path
covers the whole universe every night (see `analytics/scoring_batch.py`'s docstring for why
top-N slicing never happens on the write side), so a "top 20 bullish" query here is just reading
back an already-complete ranking, never a live rescan.

`data_class = DataClass.PUBLIC`: every input factor (price, institutional-flow, financial-
statement, market-value line items) is already classed `public` elsewhere in this codebase
(`get_institutional_flow`, `get_financial_statement`, the theme tools) — this derived read
inherits that same classification, following the established "a derived-store read is classed by
what it exposes" convention (see `analytics/screener/store.py`'s and `tools/theme_tools.py`'s own
docstrings for the same reasoning applied to their datasets).
"""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.models import ScoreProfile, ScoreResult
from analytics.scoring_store import LeaderboardDirection, get_cached_score, get_leaderboard
from schemas.enums import DataClass, Market
from tools.base import BaseTool, Tool, ToolContext, parse_args

_DEFAULT_LEADERBOARD_LIMIT = 20
_MAX_LEADERBOARD_LIMIT = 50


class GetStockScoreInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '2330'.")
    market: Market = Field(description="Exchange market the symbol id is scoped to, e.g. 'TW'.")
    profile: ScoreProfile = Field(
        default=ScoreProfile.SWING,
        description="Which horizon-weight profile to read — 'swing' (default) or 'position'.",
    )


class GetStockScoreOutput(BaseModel):
    """The detail-page read: the cached `ScoreResult` if today's batch has computed it, or an
    honest `found=False` with a plain-language reason when it has not (never a fabricated or
    stale-silently-served number)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    found: bool
    symbol: str
    market: Market
    profile: ScoreProfile
    result: ScoreResult | None = None
    message: str = ""


class GetStockScoreTool(BaseTool):
    """`get_stock_score` — one symbol's cached composite score + full factor breakdown, for the
    stock-detail-page card (momentum/trend_quality/value_guard/quality/revenue_flow bars).
    Cache-only by design — see the module docstring for why this never live-computes."""

    name = "get_stock_score"
    description = (
        "Get one stock/ETF's cached multi-factor composite score (0-100), descriptive band "
        "(偏多/中性/偏空), and the five-factor breakdown behind it. Descriptive analysis only, "
        "never a buy/sell/hold signal. Returns found=False if today's batch has not yet "
        "computed a row for this symbol."
    )
    input_model: type[BaseModel] = GetStockScoreInput
    output_model: type[BaseModel] = GetStockScoreOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "stock_score"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetStockScoreInput, args)
        async with self._session_factory() as session:
            result = await get_cached_score(
                session, symbol=typed.id, market=typed.market, profile=typed.profile
            )
        if result is None:
            return GetStockScoreOutput(
                found=False,
                symbol=typed.id,
                market=typed.market,
                profile=typed.profile,
                message=(
                    f"No score has been computed yet for {typed.id} ({typed.market.value}, "
                    f"{typed.profile.value} profile) — the daily scoring batch has not yet run "
                    "or this symbol has no ingested data."
                ),
            )
        return GetStockScoreOutput(
            found=True,
            symbol=typed.id,
            market=typed.market,
            profile=typed.profile,
            result=result,
        )


class GetScoreLeaderboardInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market = Market.TW
    profile: ScoreProfile = Field(
        default=ScoreProfile.SWING,
        description="Which horizon-weight profile to rank by — 'swing' (default) or 'position'.",
    )
    direction: LeaderboardDirection = Field(
        default="bullish",
        description="'bullish' — strongest composite first; 'bearish' — weakest composite first.",
    )
    limit: int = Field(
        default=_DEFAULT_LEADERBOARD_LIMIT,
        ge=1,
        le=_MAX_LEADERBOARD_LIMIT,
        description="Top-N rows to return (max 50) — a read-time slice of the whole-universe "
        "cache the nightly batch already computed; this never triggers a live rescan.",
    )
    as_of: date_ | None = Field(
        default=None,
        description="Score the leaderboard as of this date; omitted = latest batch on file.",
    )


class GetScoreLeaderboardOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    profile: ScoreProfile
    direction: LeaderboardDirection
    as_of: date_ | None = None
    results: tuple[ScoreResult, ...] = Field(default_factory=tuple)
    message: str = ""


class GetScoreLeaderboardTool(BaseTool):
    """`get_score_leaderboard` — the top/bottom-N whole-TW-universe composite-score ranking, read
    straight off the nightly batch's cache. Never computed live — see the module docstring."""

    name = "get_score_leaderboard"
    description = (
        "Get the top-N (max 50) strongest ('bullish') or weakest ('bearish') stocks/ETFs by "
        "cached multi-factor composite score across the whole TW universe, for a given horizon "
        "profile. Descriptive ranking only, never a buy/sell/hold recommendation list."
    )
    input_model: type[BaseModel] = GetScoreLeaderboardInput
    output_model: type[BaseModel] = GetScoreLeaderboardOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "score_leaderboard"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetScoreLeaderboardInput, args)
        async with self._session_factory() as session:
            results = await get_leaderboard(
                session,
                market=typed.market,
                profile=typed.profile,
                direction=typed.direction,
                limit=typed.limit,
                as_of=typed.as_of,
            )
        message = (
            ""
            if results
            else f"No scored rows on file yet for {typed.market.value} ({typed.profile.value} "
            "profile) — the daily scoring batch has not yet run."
        )
        return GetScoreLeaderboardOutput(
            market=typed.market,
            profile=typed.profile,
            direction=typed.direction,
            as_of=results[0].as_of if results else typed.as_of,
            results=results,
            message=message,
        )


def build_registration_table(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table. Both are deferred long-tail reads,
    parameterized by the DB session factory rather than the data-access router (they read the
    relational score cache, not a live source) — matching `theme_tools.py`'s pattern."""
    return [
        (GetStockScoreTool(session_factory), False),
        (GetScoreLeaderboardTool(session_factory), False),
    ]


__all__ = [
    "GetScoreLeaderboardInput",
    "GetScoreLeaderboardOutput",
    "GetScoreLeaderboardTool",
    "GetStockScoreInput",
    "GetStockScoreOutput",
    "GetStockScoreTool",
    "build_registration_table",
]
