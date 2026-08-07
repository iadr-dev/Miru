"""`AgentLoop.run_turn` — the Step loop's happy path, its tool-dispatch/entitlement
composition with governance, its non-trading output enforcement, and its step budget."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.loop import AgentLoop, StepBudgetExceededError
from config.settings import Settings
from llm.base import StopReason, TokenUsage
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.agent.conftest import EchoTool
from tests.llm.stubs import StubLLMClient, make_text_response, make_tool_call_response
from tests.tools.conftest import make_router
from tools import build_default_tool_registry
from tools.base import ToolContext
from tools.market_tools import GetChipFlowsTool

# The full card-opted-in tool set: every tool whose typed output is chart/table/gauge-shaped
# enough to render a `RichCard`, mapped to its stable `card_type` label. Locked here so a typo
# or an accidental rename in any tool module is caught, and so the opted-in set stays an
# explicit, reviewable list rather than something spread silently across a dozen tool files.
CARD_OPTED_TOOLS: dict[str, str] = {
    # market_tools.py
    "get_price_quote": "price_quote",
    "get_chip_flows": "chip_flows",
    "get_active_etf_report": "active_etf_report",
    "get_etf_money_flow": "etf_money_flow",
    "get_company_profile": "company_profile",
    "get_dividend_schedule": "dividend_schedule",
    "get_institutional_flow": "institutional_flow",
    # chip_tools.py
    "get_margin_short": "margin_short",
    "get_margin_short_total_market": "margin_short_total_market",
    "get_institutional_flow_wide": "institutional_flow_wide",
    "get_institutional_flow_total_market": "institutional_flow_total_market",
    "get_foreign_shareholding": "foreign_shareholding",
    "get_shareholder_dispersion": "shareholder_dispersion",
    "get_securities_lending": "securities_lending",
    "get_daily_short_sale_balances": "daily_short_sale_balances",
    "get_government_bank_flows": "government_bank_flows",
    "get_margin_maintenance": "margin_maintenance",
    "get_chip_broker_agg": "chip_broker_agg",
    "get_block_trade_broker_report": "block_trade_broker_report",
    "get_block_trade": "block_trade",
    "get_loan_collateral_balance": "loan_collateral_balance",
    "get_industry_money_flow": "industry_money_flow",
    # technical_tools.py
    "get_intraday_kbar": "intraday_kbar",
    "get_ten_year_price": "ten_year_price",
    "get_price_multi_tf": "price_multi_tf",
    "get_price_tick": "price_tick",
    "get_valuation_ratios": "valuation_ratios",
    "get_order_book_stats": "order_book_stats",
    "get_taiex_index_snapshot": "taiex_index_snapshot",
    "get_day_trading": "day_trading",
    "get_total_return_index": "total_return_index",
    "get_index_snapshots": "index_snapshots",
    "get_price_limit": "price_limit",
    # derivatives_tools.py
    "get_futures_options_daily": "futures_options_daily",
    "get_derivatives_institutional": "derivatives_institutional",
    "get_put_call_ratio": "put_call_ratio",
    "get_taiex_vix": "taiex_vix",
    "get_dealer_volume": "dealer_volume",
    "get_futures_spread_trading": "futures_spread_trading",
    "get_asset_swap_daily": "asset_swap_daily",
    # fundamental_tools.py
    "get_financial_statement": "financial_statement",
    "get_dividend_result": "dividend_result",
    "get_month_revenue": "month_revenue",
    "get_market_value": "market_value",
    "get_market_value_weight": "market_value_weight",
    # analyst_tools.py
    "get_options_chain": "options_chain",
    "get_institutional_holders": "institutional_holders",
    "get_insider_activity": "insider_activity",
    "get_fund_holdings": "fund_holdings",
    "get_sector_overview": "sector_overview",
    "get_industry_overview": "industry_overview",
    "run_equity_screener_us": "equity_screener",
    # convertible_bond_tools.py
    "get_convertible_bond_profile": "cb_profile",
    "get_convertible_bond_flow": "cb_flow",
    # macro_tools.py
    "get_macro_indicators": "macro_indicators",
    "get_business_indicator": "business_indicator",
    # etf_pcf_tools.py
    "get_etf_fund_info": "etf_fund_info",
    # etf_overlap_tools.py
    "get_etf_overlap": "etf_overlap",
    "get_fund_comparison": "fund_comparison",
    # mops_tools.py
    "get_insider_shareholding": "insider_shareholding",
    # live_tools.py
    "get_live_quote_snapshot": "live_quote_snapshot",
    # international_tools.py
    "get_international_stock": "international_stock",
    "get_us_stock_price_minute": "us_price_minute",
    # theme_tools.py
    "get_theme_members": "theme_members",
    "get_symbol_themes": "symbol_themes",
    "get_theme_tiers": "theme_tiers",
    # score_tools.py
    "get_stock_score": "stock_score",
    "get_score_leaderboard": "score_leaderboard",
}


def _theme_session_factory() -> async_sessionmaker:
    """A bare session factory for registry-metadata tests: the theme tools need one at
    construction so they register, but these tests only inspect tool metadata (name/card_type/
    schema), never call `run()`, so the engine is never actually queried."""
    return async_sessionmaker(create_async_engine("sqlite+aiosqlite:///:memory:"))


def _chip_broker_result() -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.SPONSOR,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="chip caveat",
        payload={"rows": []},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


def _make_chip_flows_tool() -> GetChipFlowsTool:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {Capability.CHIP_BROKER: _chip_broker_result()})
    return GetChipFlowsTool(router)


async def test_run_turn_returns_final_text_when_model_answers_immediately() -> None:
    llm = StubLLMClient(script=[make_text_response("Here is some context.")])
    loop = AgentLoop(
        llm, tools=[EchoTool()], context_window=128_000, context_reserve=4_000, max_steps=4
    )

    result = await loop.run_turn("what's up with 2330?", tool_context=ToolContext())

    assert "Here is some context." in result.final_text
    assert len(result.steps) == 1
    # non-trading caveat appended since no caveat was present in the model's own text.
    assert "not a buy/sell/hold recommendation" in result.final_text


async def test_run_turn_drives_full_step_sequence_through_dispatch_tool() -> None:
    """A scripted tool-call proposal for `echo_tool` walks
    step_start -> transform_context -> llm.chat -> pre_tool -> dispatch -> post_tool ->
    (next Step) -> llm.chat -> pre_output -> step_end."""
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "hello"}
            ),
            make_text_response("The tool said hello back."),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4)

    result = await loop.run_turn("echo hello", tool_context=ToolContext())

    assert len(result.steps) == 2
    first_step = result.steps[0]
    assert len(first_step.tool_messages) == 1
    tool_result_message = first_step.tool_messages[0]
    assert tool_result_message.tool_results[0].is_error is False
    assert "EchoOutput" in tool_result_message.tool_results[0].content
    # journal recorded the dispatch with an allowed verdict and no raw payload.
    journal_entries = result.journal.entries
    tool_call_entries = [e for e in journal_entries if e.kind == "tool_call"]
    assert len(tool_call_entries) == 1
    assert tool_call_entries[0].guardrail_allowed is True
    assert "hello" not in (tool_call_entries[0].result_summary or "")


async def test_run_turn_dispatches_sponsor_tool_through_entitlement_gate_free_tier() -> None:
    """A scripted proposal for the sponsor-tier `get_chip_flows` worked example is masked for
    a FREE-tier caller — proving the entitlement gate fires *inside the loop*, not just when
    `dispatch_tool` is called in isolation."""
    tool = _make_chip_flows_tool()
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1",
                name="get_chip_flows",
                arguments={"id": "2330", "market": "TW"},
            ),
            make_text_response("Chip flow context follows."),
        ]
    )
    loop = AgentLoop(llm, tools=[tool], max_steps=4)

    result = await loop.run_turn(
        "show me chip flows for 2330", tool_context=ToolContext(tier=Tier.FREE)
    )

    tool_result_message = result.steps[0].tool_messages[0]
    assert "MaskedTeaser" in tool_result_message.tool_results[0].content
    # The sponsor tool is card-opted, but a FREE caller's dispatch is masked *before* a card is
    # ever built — so no card carrying real sponsor data can reach the unentitled caller.
    assert result.rich_cards == ()


async def test_run_turn_dispatches_sponsor_tool_for_entitled_tier() -> None:
    tool = _make_chip_flows_tool()
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1",
                name="get_chip_flows",
                arguments={"id": "2330", "market": "TW"},
            ),
            make_text_response("Chip flow context follows."),
        ]
    )
    loop = AgentLoop(llm, tools=[tool], max_steps=4)

    result = await loop.run_turn(
        "show me chip flows for 2330", tool_context=ToolContext(tier=Tier.PREMIUM)
    )

    tool_result_message = result.steps[0].tool_messages[0]
    assert "GetChipFlowsOutput" in tool_result_message.tool_results[0].content
    assert "MaskedTeaser" not in tool_result_message.tool_results[0].content
    # An entitled caller's real result does become a governed card.
    assert len(result.rich_cards) == 1
    card = result.rich_cards[0]
    assert card.card_type == "chip_flows"
    assert card.data["source_id"] == "STUB"
    assert "not a buy/sell/hold recommendation" in card.data["non_trading_caveat"]


async def test_run_turn_builds_no_card_for_a_non_card_opted_tool() -> None:
    """A tool that hasn't opted in (`card_type` stays `None`) produces no card — the default,
    behavior-preserving path for the vast majority of tools."""
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "hello"}
            ),
            make_text_response("The tool said hello back."),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4)

    result = await loop.run_turn("echo hello", tool_context=ToolContext())

    assert result.rich_cards == ()


@pytest.mark.parametrize(("tool_name", "expected_card_type"), sorted(CARD_OPTED_TOOLS.items()))
def test_card_opted_tool_declares_its_exact_card_type(
    tool_name: str, expected_card_type: str
) -> None:
    """Every card-opted-in tool declares exactly the `card_type` label the frontend renders
    against — a lock so an accidental rename/typo in any tool module is caught here rather than
    silently shipping a card the frontend can't map."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {})
    registry = build_default_tool_registry(router, session_factory=_theme_session_factory())

    tool = registry.get_tool(tool_name)
    assert tool is not None, f"{tool_name} is not registered"
    assert tool.card_type == expected_card_type


def test_exactly_the_card_opted_tools_declare_a_card_type() -> None:
    """No tool outside the reviewed opt-in set carries a `card_type` — guards against a card
    being enabled somewhere without landing in the locked mapping above."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {})
    registry = build_default_tool_registry(router, session_factory=_theme_session_factory())

    opted_in = {t.name: t.card_type for t in registry.all_tools() if t.card_type is not None}
    assert opted_in == CARD_OPTED_TOOLS


async def test_run_turn_blocks_full_market_scan_tool_argument_before_dispatch() -> None:
    """`pre_tool` vetoes a full-market-scan argument before `dispatch_tool` ever runs — the
    tool never executes."""
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "all"}
            ),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4)

    result = await loop.run_turn("scan the market", tool_context=ToolContext())

    tool_result_message = result.steps[0].tool_messages[0]
    assert tool_result_message.tool_results[0].is_error is True
    assert "blocked" in tool_result_message.tool_results[0].content


async def test_run_turn_redacts_trading_language_before_it_reaches_the_caller() -> None:
    llm = StubLLMClient(
        script=[make_text_response("You should buy this stock right now, strong buy!")]
    )
    loop = AgentLoop(llm, tools=[], max_steps=4)

    result = await loop.run_turn("should I buy 2330?", tool_context=ToolContext())

    assert "buy" not in result.final_text.lower() or "redacted" in result.final_text.lower()
    assert "should buy" not in result.final_text.lower()
    assert "[redacted" in result.final_text
    guardrail_entries = [e for e in result.journal.entries if e.kind == "guardrail_output"]
    assert len(guardrail_entries) == 1
    assert guardrail_entries[0].guardrail_reason == "trading_language_redacted"


async def test_run_turn_raises_step_budget_exceeded_when_model_never_finalizes() -> None:
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id=f"call-{i}", name="echo_tool", arguments={"text": "again"}
            )
            for i in range(3)
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=3)

    with pytest.raises(StepBudgetExceededError):
        await loop.run_turn("keep going forever", tool_context=ToolContext())


async def test_run_turn_reports_unknown_tool_without_dispatching() -> None:
    llm = StubLLMClient(
        script=[
            make_tool_call_response(tool_call_id="call-1", name="not_a_real_tool", arguments={}),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4)

    result = await loop.run_turn("call a fake tool", tool_context=ToolContext())

    tool_result_message = result.steps[0].tool_messages[0]
    assert tool_result_message.tool_results[0].is_error is True
    assert "unknown tool" in tool_result_message.tool_results[0].content


async def test_run_turn_uses_settings_defaults_when_no_explicit_budget_given() -> None:
    """Constructing an `AgentLoop` without explicit context/step knobs reads
    `Settings.aitw_context_window_tokens`/`aitw_context_reserve_tokens`/`aitw_agent_max_steps`
    rather than a hardcoded literal."""
    settings = Settings(aitw_agent_max_steps=1)
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "x"}
            )
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], settings=settings)

    with pytest.raises(StepBudgetExceededError):
        await loop.run_turn("hi", tool_context=ToolContext())


async def test_stop_reason_and_usage_are_carried_through_step_result() -> None:
    usage = TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10)
    llm = StubLLMClient(script=[make_text_response("ok", usage=usage)])
    loop = AgentLoop(llm, tools=[], max_steps=2)

    result = await loop.run_turn("hi", tool_context=ToolContext())

    assert result.steps[0].response.stop_reason == StopReason.END_TURN
    assert result.steps[0].response.usage.total_tokens == 10
