"""`get_stock_summary` driven through the REAL registered tool + `dispatch_tool` path, against a
seeded relational score cache and a stub `LLMClient` — no live provider, no upstream fetch.

Covers: no-score-yet -> `found=False` (no LLM call); cached-summary-row hit -> served without a
second LLM call; cache-miss -> the stub's scripted JSON is parsed, persisted, and returned; and a
deliberately "dirty" stubbed response containing trading language is stripped by
`agent.guardrails.scan_structured_payload` before it ever reaches the final `StockSummaryResult`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from analytics.models import (
    FactorScore,
    ScoreBand,
    ScoreProfile,
    ScoreResult,
    StockScoreBreakdown,
)
from analytics.score import unavailable_factor
from analytics.scoring_store import upsert_score
from analytics.symbol_universe import SymbolType
from db import models  # noqa: F401  # pyright: ignore[reportUnusedImport] — registers ORM models
from db.base import Base
from db.sqlite_pragma import enable_sqlite_foreign_keys
from schemas.enums import Market, Tier, TLSPolicy
from tests.llm.stubs import StubLLMClient, make_text_response
from tools.base import ToolContext, dispatch_tool
from tools.summary_tools import GetStockSummaryOutput, build_registration_table


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    enable_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _score_result(as_of: date = date(2026, 9, 22)) -> ScoreResult:
    breakdown = StockScoreBreakdown(
        momentum=FactorScore(name="momentum", percentile=80.0, description="strong 12-1 momentum"),
        trend_quality=FactorScore(
            name="trend_quality", percentile=70.0, description="above MA150/200"
        ),
        value_guard=unavailable_factor("value_guard", "no financials ingested"),
        quality=unavailable_factor("quality", "no financials ingested"),
        revenue_flow=FactorScore(
            name="revenue_flow", percentile=55.0, description="flat revenue trend"
        ),
    )
    return ScoreResult(
        symbol="2330",
        market=Market.TW,
        as_of=as_of,
        profile=ScoreProfile.SWING,
        symbol_type=SymbolType.STOCK,
        composite=68.0,
        band=ScoreBand.BULLISH_LEANING,
        breakdown=breakdown,
        partial_factors=("value_guard", "quality"),
        regime_caveat="regime caveat text",
        non_trading_caveat="This is analytical market context only, not a recommendation.",
        normalization_caveat=(
            "Compared against the whole universe (no sector classification ingested)."
        ),
    )


def _premium_ctx() -> ToolContext:
    return ToolContext(tier=Tier.PREMIUM, tls_policy=TLSPolicy.STRICT)


def _clean_script_response(text: str = "") -> object:
    return make_text_response(
        text
        or json.dumps(
            {
                "headline": "偏多格局，動能與趨勢皆強",
                "narrative": (
                    "動能因子與趨勢品質因子皆位於高百分位，顯示近期價格表現偏強；營收流因子中性。"
                ),
                "strengths": ["momentum factor at the 80th percentile"],
                "weaknesses": ["value_guard factor unavailable — no financials ingested"],
                "opportunities": ["trend_quality factor above MA150/200 suggests continuation"],
                "threats": [],
            }
        )
    )


async def test_no_score_yet_returns_found_false_without_llm_call(
    session_factory: async_sessionmaker,
) -> None:
    llm = StubLLMClient(script=[])
    table = build_registration_table(session_factory, llm)
    tool = table[0][0]

    result = await dispatch_tool(
        tool, tool.input_model(id="2330", market=Market.TW), _premium_ctx()
    )

    assert isinstance(result, GetStockSummaryOutput)
    assert result.found is False
    assert "not computed yet" in result.message or "No score" in result.message
    assert llm.calls == []


async def test_cache_miss_generates_and_persists_summary(
    session_factory: async_sessionmaker,
) -> None:
    score = _score_result()
    async with session_factory() as session:
        await upsert_score(session, score)
        await session.commit()

    llm = StubLLMClient(script=[_clean_script_response()])
    table = build_registration_table(session_factory, llm)
    tool = table[0][0]

    result = await dispatch_tool(
        tool, tool.input_model(id="2330", market=Market.TW), _premium_ctx()
    )

    assert isinstance(result, GetStockSummaryOutput)
    assert result.found is True
    assert result.as_of == score.as_of
    assert result.headline == "偏多格局，動能與趨勢皆強"
    assert result.swot.strengths == ("momentum factor at the 80th percentile",)
    assert len(llm.calls) == 1

    # Persisted — a fresh tool instance over the same DB serves it without calling the LLM again.
    llm2 = StubLLMClient(script=[])
    table2 = build_registration_table(session_factory, llm2)
    tool2 = table2[0][0]
    result2 = await dispatch_tool(
        tool2, tool2.input_model(id="2330", market=Market.TW), _premium_ctx()
    )
    assert isinstance(result2, GetStockSummaryOutput)
    assert result2.headline == result.headline
    assert llm2.calls == []


async def test_cached_summary_row_hit_never_calls_llm_again(
    session_factory: async_sessionmaker,
) -> None:
    score = _score_result()
    async with session_factory() as session:
        await upsert_score(session, score)
        await session.commit()

    llm = StubLLMClient(script=[_clean_script_response()])
    table = build_registration_table(session_factory, llm)
    tool = table[0][0]
    args = tool.input_model(id="2330", market=Market.TW)

    first = await dispatch_tool(tool, args, _premium_ctx())
    assert isinstance(first, GetStockSummaryOutput)
    assert len(llm.calls) == 1

    second = await dispatch_tool(tool, args, _premium_ctx())
    assert isinstance(second, GetStockSummaryOutput)
    assert second.headline == first.headline
    # No second LLM call — served from the cached row.
    assert len(llm.calls) == 1


async def test_dirty_llm_response_trading_language_is_redacted_before_persisting(
    session_factory: async_sessionmaker,
) -> None:
    score = _score_result()
    async with session_factory() as session:
        await upsert_score(session, score)
        await session.commit()

    dirty_payload = json.dumps(
        {
            "headline": "you should buy this stock now",
            "narrative": "Momentum is strong. You should buy this stock now given the trend.",
            "strengths": ["strong buy rating based on momentum factor"],
            "weaknesses": [],
            "opportunities": [],
            "threats": [],
        }
    )
    llm = StubLLMClient(script=[make_text_response(dirty_payload)])
    table = build_registration_table(session_factory, llm)
    tool = table[0][0]

    result = await dispatch_tool(
        tool, tool.input_model(id="2330", market=Market.TW), _premium_ctx()
    )

    assert isinstance(result, GetStockSummaryOutput)
    assert result.found is True
    assert "buy" not in result.headline.lower()
    assert "buy" not in result.narrative.lower()
    assert all("buy" not in bullet.lower() for bullet in result.swot.strengths)
