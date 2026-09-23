"""The AI 智能摘要 tool — the agent's read surface onto the cached `stock_summaries` table
(`analytics/summary_store.py`), generating a new row on demand from the day's cached
`ScoreResult` (`analytics/scoring_store.py::get_cached_score`) when one doesn't exist yet.

`data_class = DataClass.SPONSOR`: this is a genuinely new, LLM-synthesized product surface
(headline/narrative/SWOT), not a raw pass-through of an already-`public` dataset like
`score_tools.py`'s cache reads — it is gated as a Sponsor-tier feature per the phase brief, so an
unentitled caller gets the whole-tool masked-teaser envelope (`tools.base.dispatch_tool`), never
a partial/real result.

Cache-then-generate, never live-recompute-the-score: this tool only ever reads today's already-
computed `ScoreResult` (`ScoreProfile.SWING`) — if the daily scoring batch hasn't run yet for this
symbol, there is nothing to ground a summary in, so this tool returns `found=False` rather than
inventing one. Once a score exists, a cached summary for that exact `(symbol, market, as_of)` is
served without calling the LLM again; only a genuine cache miss triggers `generate_stock_summary`.
"""

from __future__ import annotations

from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from analytics.models import ScoreProfile, StockSwot
from analytics.scoring_store import get_cached_score
from analytics.summary_generation import generate_stock_summary
from analytics.summary_store import get_cached_summary, upsert_summary
from llm.base import LLMClient
from schemas.enums import DataClass, Market
from tools.base import BaseTool, Tool, ToolContext, parse_args


class GetStockSummaryInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(min_length=1, description="Exchange-scoped symbol id, e.g. '2330'.")
    market: Market = Field(description="Exchange market the symbol id is scoped to, e.g. 'TW'.")


class GetStockSummaryOutput(BaseModel):
    """The detail-page read: the AI-generated headline/narrative/SWOT grounded in today's cached
    composite score, or an honest `found=False` when no score exists yet to ground it in (never
    a fabricated summary)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    found: bool
    symbol: str
    market: Market
    as_of: date_ | None = None
    headline: str = ""
    narrative: str = ""
    swot: StockSwot = Field(default_factory=StockSwot)
    non_trading_caveat: str = ""
    message: str = ""


class GetStockSummaryTool(BaseTool):
    """`get_stock_summary` — one symbol's AI-generated headline + narrative + SWOT, strictly
    grounded in the cached multi-factor score. Never live-computes a score itself; see the module
    docstring for why a missing score means an honest `found=False`, not a fallback computation."""

    name = "get_stock_summary"
    description = (
        "Get an AI-generated headline, narrative, and SWOT summary for one stock/ETF, strictly "
        "grounded in its already-computed multi-factor composite score (never fabricated beyond "
        "that data). Descriptive analysis only, never a buy/sell/hold signal. Returns "
        "found=False if no score has been computed yet for this symbol to ground a summary in."
    )
    input_model: type[BaseModel] = GetStockSummaryInput
    output_model: type[BaseModel] = GetStockSummaryOutput
    data_class = DataClass.SPONSOR
    card_type: str | None = "stock_summary"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession], llm: LLMClient) -> None:
        self._session_factory = session_factory
        self._llm = llm

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        typed = parse_args(GetStockSummaryInput, args)
        async with self._session_factory() as session:
            score = await get_cached_score(
                session, symbol=typed.id, market=typed.market, profile=ScoreProfile.SWING
            )
            if score is None:
                return GetStockSummaryOutput(
                    found=False,
                    symbol=typed.id,
                    market=typed.market,
                    message=(
                        f"No score has been computed yet for {typed.id} ({typed.market.value}) "
                        "— score not computed yet, so there is nothing to ground a summary in."
                    ),
                )

            cached = await get_cached_summary(
                session, symbol=typed.id, market=typed.market, trade_date=score.as_of
            )
            if cached is not None:
                return GetStockSummaryOutput(
                    found=True,
                    symbol=cached.symbol,
                    market=cached.market,
                    as_of=cached.as_of,
                    headline=cached.headline,
                    narrative=cached.narrative,
                    swot=cached.swot,
                    non_trading_caveat=cached.non_trading_caveat,
                )

            generated = await generate_stock_summary(score, self._llm)
            await upsert_summary(session, generated)
            await session.commit()

        return GetStockSummaryOutput(
            found=True,
            symbol=generated.symbol,
            market=generated.market,
            as_of=generated.as_of,
            headline=generated.headline,
            narrative=generated.narrative,
            swot=generated.swot,
            non_trading_caveat=generated.non_trading_caveat,
        )


def build_registration_table(
    session_factory: async_sessionmaker[AsyncSession], llm: LLMClient
) -> list[tuple[Tool, bool]]:
    """This module's `(tool, immediate)` registration table — a deferred long-tail read,
    parameterized by both the DB session factory (the score cache + summary cache) and an
    `LLMClient` (resolved via `llm.registry.resolve_role(ModelRole.SUBAGENT)` at the composition
    root), matching `score_tools.py`'s pattern plus the one extra collaborator this tool needs."""
    return [(GetStockSummaryTool(session_factory, llm), False)]


__all__ = [
    "GetStockSummaryInput",
    "GetStockSummaryOutput",
    "GetStockSummaryTool",
    "build_registration_table",
]
