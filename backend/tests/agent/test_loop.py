"""`AgentLoop.run_turn` — the Step loop's happy path, its tool-dispatch/entitlement
composition with governance, its non-trading output enforcement, and its step budget."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent.guardrails import REDACTION_NOTICE_EN
from agent.loop import AgentLoop, StepBudgetExceededError
from config.settings import Settings
from llm.base import Message, MessageRole, StopReason, TokenUsage, WebSearchSource
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.agent.conftest import EchoTool
from tests.llm.stubs import StubLLMClient, make_text_response, make_tool_call_response
from tests.tools.conftest import make_router
from tools import build_default_tool_registry
from tools.base import ToolContext
from tools.market import GetChipFlowsTool
from tools.propose_alert_tools import ProposeAlertTool

# The full card-opted-in tool set: every tool whose typed output is chart/table/gauge-shaped
# enough to render a `RichCard`, mapped to its stable `card_type` label. Locked here so a typo
# or an accidental rename in any tool module is caught, and so the opted-in set stays an
# explicit, reviewable list rather than something spread silently across a dozen tool files.
CARD_OPTED_TOOLS: dict[str, str] = {
    # tools/market/
    "get_price_quote": "price_quote",
    "get_live_price_quote": "live_price_quote",
    "get_chip_flows": "chip_flows",
    "get_active_etf_report": "active_etf_report",
    "get_etf_money_flow": "etf_money_flow",
    "get_etfs_holding_stock": "etfs_holding_stock",
    "get_daily_signal": "daily_signal",
    "get_advance_decline": "advance_decline",
    "get_index_contribution": "index_contribution",
    "get_sector_heatmap": "sector_heatmap",
    "get_sector_constituents": "sector_constituents",
    "get_stock_heatmap": "stock_heatmap",
    "get_sector_rotation": "sector_rotation",
    "get_sector_rotation_rrg": "sector_rotation_rrg",
    "get_company_profile": "company_profile",
    "get_dividend_schedule": "dividend_schedule",
    "get_institutional_flow": "institutional_flow",
    "get_price_momentum": "price_momentum",
    "get_holder_accumulation": "holder_accumulation",
    "get_symbol_directory_search": "symbol_search",
    "get_price_history": "price_history",
    "get_us_market_rankings": "us_market_ranking",
    "get_tw_market_rankings": "tw_market_ranking",
    "get_macro_calendar": "macro_calendar",
    "get_etf_list": "etf_list",
    "get_dividend_calendar": "dividend_calendar",
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
    "get_technical_indicators": "technical_indicators",
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
    "get_dividend_policy_summary": "dividend_policy_summary",
    "get_month_revenue": "month_revenue",
    "get_market_value": "market_value",
    "get_market_value_weight": "market_value_weight",
    # analyst_tools.py
    "get_options_chain": "options_chain",
    "get_institutional_holders": "institutional_holders",
    "get_insider_activity": "insider_activity",
    "get_fund_holdings": "fund_holdings",
    "get_fund_profile": "fund_profile",
    "get_sector_overview": "sector_overview",
    "get_industry_overview": "industry_overview",
    "get_stock_peers": "stock_peers",
    "run_equity_screener_us": "equity_screener",
    "get_analyst_consensus": "analyst_consensus",
    "get_esg_score": "esg_score",
    "get_sec_filings": "sec_filings",
    # convertible_bond_tools.py
    "get_convertible_bond_profile": "cb_profile",
    "get_convertible_bond_flow": "cb_flow",
    # macro_tools.py
    "get_macro_indicators": "macro_indicators",
    "get_business_indicator": "business_indicator",
    # etf_pcf_tools.py
    "get_etf_fund_info": "etf_fund_info",
    "get_etf_expense_ratio": "etf_expense_ratio",
    "get_etf_theme_tags": "etf_theme_tags",
    "get_etf_trailing_returns": "etf_trailing_returns",
    "get_etf_premium_discount": "etf_premium_discount",
    # etf_overlap_tools.py
    "get_etf_overlap": "etf_overlap",
    "get_fund_comparison": "fund_comparison",
    # mops_tools.py
    "get_insider_shareholding": "insider_shareholding",
    "get_material_announcement_feed": "material_announcement_feed",
    "get_insider_transfer_declarations": "insider_transfer_declaration",
    "get_self_assessed_earnings": "self_assessed_earnings",
    # live_tools.py
    "get_live_quote_snapshot": "live_quote_snapshot",
    # international_tools.py
    "get_international_stock": "international_stock",
    "get_us_stock_price_minute": "us_price_minute",
    # theme_tools.py
    "get_themes": "themes",
    "get_theme_members": "theme_members",
    "get_symbol_themes": "symbol_themes",
    "get_theme_tiers": "theme_tiers",
    "get_supply_chain_edges": "supply_chain_edges",
    "get_theme_tier_heatmap": "theme_tier_heatmap",
    # score_tools.py
    "get_stock_score": "stock_score",
    "get_score_leaderboard": "score_leaderboard",
    # summary_tools.py
    "get_stock_summary": "stock_summary",
    # trend_score_tools.py
    "get_trend_score": "trend_score",
    # tdcc_tools.py
    "get_tdcc_shareholding_distribution": "tdcc_shareholding_distribution",
    "get_tdcc_evoting_schedule": "tdcc_evoting_schedule",
    # ask_user_tools.py
    "ask_user": "ask_user",
    # propose_alert_tools.py
    "propose_alert": "alert_proposal",
    "propose_memory": "memory_proposal",
    "propose_watchlist_change": "watchlist_proposal",
    "propose_schedule": "schedule_proposal",
    "get_annual_fundamentals": "annual_fundamentals",
    "get_revenue_seasonality": "revenue_seasonality",
    "get_risk_metrics": "risk_metrics",
    "get_etf_annual_summary": "etf_annual_summary",
    "get_comparison_metrics": "comparison_metrics",
    "get_morning_brief": "morning_brief",
    # market_index_strip_tools.py
    "get_market_index_strip": "market_index_strip",
    # us_market_index_strip_tools.py
    "get_us_market_index_strip": "us_market_index_strip",
    # disposition_board_tools.py
    "get_disposition_board": "disposition_board",
}


def _theme_session_factory() -> async_sessionmaker:
    """A bare session factory for registry-metadata tests: the theme tools need one at
    construction so they register, but these tests only inspect tool metadata (name/card_type/
    schema), never call `run()`, so the engine is never actually queried."""
    return async_sessionmaker(create_async_engine("sqlite+aiosqlite:///:memory:"))


def _summary_llm_stub() -> StubLLMClient:
    """A bare stub `LLMClient` for registry-metadata tests: `get_stock_summary` needs one at
    construction so it registers, but these tests never call `run()`, so it is never invoked."""
    return StubLLMClient(script=[])


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


async def test_run_turn_splices_history_between_system_and_current_user_message() -> None:
    """Without `history`, a follow-up like "what about its dividend history?" has no way to
    resolve "its" — this proves prior turns actually reach the model, positioned after the system
    prompt and before the current turn's own user message, not appended/prepended incorrectly."""
    llm = StubLLMClient(script=[make_text_response("2330 paid NT$3.5/share last quarter.")])
    loop = AgentLoop(llm, tools=[], max_steps=1)
    history = (
        Message(role=MessageRole.USER, content="tell me about 2330"),
        Message(role=MessageRole.ASSISTANT, content="2330 (TSMC) is a semiconductor foundry."),
    )

    await loop.run_turn(
        "what about its dividend history?", tool_context=ToolContext(), history=history
    )

    sent_messages, _tools = llm.calls[0]
    assert sent_messages[0].role is MessageRole.SYSTEM
    assert sent_messages[1] == history[0]
    assert sent_messages[2] == history[1]
    assert sent_messages[3].role is MessageRole.USER
    assert sent_messages[3].content == "what about its dividend history?"
    assert len(sent_messages) == 4


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
    # The model sees the real echoed value (not just the type/field names) so it can
    # accurately narrate what the tool returned.
    assert "hello" in tool_result_message.tool_results[0].content
    # journal recorded the dispatch with an allowed verdict and no raw payload.
    journal_entries = result.journal.entries
    tool_call_entries = [e for e in journal_entries if e.kind == "tool_call"]
    assert len(tool_call_entries) == 1
    assert tool_call_entries[0].guardrail_allowed is True
    assert "hello" not in (tool_call_entries[0].result_summary or "")


async def test_run_turn_fires_pre_tool_hooks_before_dispatch() -> None:
    """`pre_tool` fires the instant a call is about to dispatch — the live "in progress" signal
    a `thinking`-hookless model still needs (see `agent.hooks.PreToolHook`'s docstring)."""
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "hello"}
            ),
            make_text_response("The tool said hello back."),
        ]
    )
    received: list[tuple[int, str]] = []

    async def hook(*, step_index: int, tool_name: str) -> None:
        received.append((step_index, tool_name))

    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4, pre_tool_hooks=[hook])

    await loop.run_turn("echo hello", tool_context=ToolContext())

    assert received == [(0, "echo_tool")]


async def test_run_turn_fires_pre_tool_hooks_even_for_a_call_governance_goes_on_to_block() -> None:
    """Unlike `post_tool` (which also observes a blocked call), `pre_tool` fires BEFORE
    `guardrails.check_tool_call` even runs — proving the hook is not gated behind the veto it
    precedes."""
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"scan_all": True}
            ),
            make_text_response("done"),
        ]
    )
    received: list[str] = []

    async def hook(*, step_index: int, tool_name: str) -> None:
        received.append(tool_name)

    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4, pre_tool_hooks=[hook])

    result = await loop.run_turn("echo hello", tool_context=ToolContext())

    assert received == ["echo_tool"]
    tool_call_entries = [e for e in result.journal.entries if e.kind == "tool_call"]
    assert tool_call_entries[0].guardrail_allowed is False


async def test_run_turn_dispatches_sponsor_tool_through_entitlement_gate_free_tier() -> None:
    """`get_chip_flows` opted into the governance.md §4 row-level preview exception
    (`supports_row_preview=True`) — a scripted proposal for a FREE-tier caller still gets a real
    (never `MaskedTeaser`) result, with its ranked rows individually masked, proving the
    entitlement gate's row-preview branch fires *inside the loop*, not just when `dispatch_tool`
    is called in isolation. The sponsor tool is also card-opted, and — unlike a non-opted-in
    sponsor tool — its row-masked-but-real result DOES reach the caller as a card, matching the
    stock-detail page's own row-masked card for the same tool."""
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
    content = tool_result_message.tool_results[0].content
    assert "MaskedTeaser" not in content
    assert '"rows":[]' in content
    assert '"top_brokers":[]' in content
    assert len(result.rich_cards) == 1


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
    # The model sees the real governed field values (source id, health status), not just the
    # bare type/field-name summary — this is what lets it accurately narrate the fetched data.
    assert "STUB" in tool_result_message.tool_results[0].content
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
    registry = build_default_tool_registry(
        router, session_factory=_theme_session_factory(), summary_llm=_summary_llm_stub()
    )

    tool = registry.get_tool(tool_name)
    assert tool is not None, f"{tool_name} is not registered"
    assert tool.card_type == expected_card_type


def test_exactly_the_card_opted_tools_declare_a_card_type() -> None:
    """No tool outside the reviewed opt-in set carries a `card_type` — guards against a card
    being enabled somewhere without landing in the locked mapping above."""
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {})
    registry = build_default_tool_registry(
        router, session_factory=_theme_session_factory(), summary_llm=_summary_llm_stub()
    )

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

    assert "should buy" not in result.final_text.lower()
    assert REDACTION_NOTICE_EN in result.final_text
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
    `Settings.context_window_tokens`/`context_reserve_tokens`/`agent_max_steps`
    rather than a hardcoded literal."""
    settings = Settings(agent_max_steps=1)
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


async def test_run_turn_forwards_thinking_deltas_to_registered_thinking_hooks() -> None:
    """`AgentLoop` now drives every Step via `llm.stream()` (not `chat()`) so a
    `ThinkingDeltaEvent` the client yields reaches every registered `thinking` hook live, as
    each chunk arrives — not buffered until the Step completes."""
    llm = StubLLMClient(
        script=[make_text_response("Here is some context.")],
        thinking_chunks=[["pondering", " further"]],
    )
    received: list[str] = []

    async def hook(*, text: str) -> None:
        received.append(text)

    loop = AgentLoop(llm, tools=[], max_steps=2, thinking_hooks=[hook])

    result = await loop.run_turn("what's up with 2330?", tool_context=ToolContext())

    assert received == ["pondering", " further"]
    assert "Here is some context." in result.final_text


async def test_run_turn_scans_thinking_deltas_for_trading_language_before_forwarding() -> None:
    """Thinking text is model-generated output like any other — it goes through the same
    `guardrails.check_output` non-trading scan as the final answer before it ever reaches a
    `thinking` hook, so a leaked buy/sell/hold signal mid-reasoning is redacted, not forwarded
    verbatim."""
    llm = StubLLMClient(
        script=[make_text_response("final answer")],
        thinking_chunks=[["You should buy this stock now."]],
    )
    received: list[str] = []

    async def hook(*, text: str) -> None:
        received.append(text)

    loop = AgentLoop(llm, tools=[], max_steps=2, thinking_hooks=[hook])

    await loop.run_turn("hi", tool_context=ToolContext())

    assert len(received) == 1
    assert "buy" not in received[0].lower()
    assert received[0] == REDACTION_NOTICE_EN


async def test_run_turn_streams_the_final_answer_sentence_by_sentence() -> None:
    """`AgentLoop` forwards each COMPLETE sentence of the final answer to every registered
    `answer_delta` hook as soon as it closes, not buffered until the whole Step completes —
    the live token-stream this hook exists for. The stub yields the answer split across
    multiple `TextDeltaEvent`s so a sentence boundary can fall mid-chunk, exactly like a real
    provider's token stream."""
    llm = StubLLMClient(
        script=[make_text_response("First sentence. Second sentence. Third.")],
        text_chunks=[["First sent", "ence. Second sen", "tence. Third."]],
    )
    received: list[str] = []

    async def hook(*, text: str) -> None:
        received.append(text)

    loop = AgentLoop(llm, tools=[], max_steps=2, answer_delta_hooks=[hook])

    result = await loop.run_turn("hi", tool_context=ToolContext())

    assert received == ["First sentence.", " Second sentence.", " Third."]
    assert "".join(received) == "First sentence. Second sentence. Third."
    assert result.final_text.startswith("First sentence. Second sentence. Third.")


async def test_run_turn_scans_streamed_sentences_for_trading_language_before_forwarding() -> None:
    """A sentence carrying trading language is redacted before it is ever forwarded to an
    `answer_delta` hook — the same non-negotiable scan `check_output`'s buffered pass applies
    to the whole answer, now applied per-sentence as each one streams live."""
    llm = StubLLMClient(
        script=[
            make_text_response("Context is fine. You should buy this stock now. More context.")
        ],
        text_chunks=[
            ["Context is fine. You should buy ", "this stock now. More context."],
        ],
    )
    received: list[str] = []

    async def hook(*, text: str) -> None:
        received.append(text)

    loop = AgentLoop(llm, tools=[], max_steps=2, answer_delta_hooks=[hook])

    await loop.run_turn("hi", tool_context=ToolContext())

    joined = "".join(received)
    assert "buy" not in joined.lower()
    assert REDACTION_NOTICE_EN in joined
    assert "Context is fine." in joined
    assert "More context." in joined


async def test_run_turn_flushes_a_trailing_sentence_fragment_with_no_terminator() -> None:
    """A short answer with no closing punctuation at all (or the final sentence, which the
    stream never explicitly terminates) is still forwarded once the stream ends — the trailing
    buffer flush, not lost simply because no delta ever closed it."""
    llm = StubLLMClient(
        script=[make_text_response("好的")],
        text_chunks=[["好", "的"]],
    )
    received: list[str] = []

    async def hook(*, text: str) -> None:
        received.append(text)

    loop = AgentLoop(llm, tools=[], max_steps=2, answer_delta_hooks=[hook])

    await loop.run_turn("hi", tool_context=ToolContext())

    assert received == ["好的"]


async def test_run_turn_answer_delta_hooks_are_a_no_op_when_none_registered() -> None:
    """No `answer_delta_hooks` configured (the default) must not change `final_text` or crash —
    the streaming path is purely additive over the existing buffered `response.text` behavior."""
    llm = StubLLMClient(script=[make_text_response("A plain answer with no hooks watching.")])
    loop = AgentLoop(llm, tools=[], max_steps=2)

    result = await loop.run_turn("hi", tool_context=ToolContext())

    assert "A plain answer with no hooks watching." in result.final_text


async def test_run_turn_forwards_web_search_sources_to_registered_web_search_hooks() -> None:
    """A `WebSearchResultEvent` the client streams reaches every registered `web_search` hook
    verbatim (no `guardrails.check_output` scan — this is structured url/title metadata, not
    prose, mirroring the `thinking` hook's forwarding but without the text-scan step)."""
    sources = (
        WebSearchSource(url="https://example.com/a", title="Source A"),
        WebSearchSource(url="https://example.com/b", title="Source B"),
    )
    llm = StubLLMClient(
        script=[make_text_response("Here is some context.")],
        web_search_sources=[sources],
    )
    received: list[tuple[WebSearchSource, ...]] = []

    async def hook(*, sources: tuple[WebSearchSource, ...]) -> None:
        received.append(sources)

    loop = AgentLoop(llm, tools=[], max_steps=2, web_search_hooks=[hook])

    result = await loop.run_turn("what's the latest on 2330?", tool_context=ToolContext())

    assert received == [sources]
    assert "Here is some context." in result.final_text


async def test_run_turn_accumulates_web_search_sources_onto_the_turn_result() -> None:
    """`TurnResult.web_search_sources` collects every `WebSearchResultEvent`'s sources across the
    whole Turn — not just what a registered `web_search` hook happens to observe — since a Turn
    can complete with no hook registered at all (e.g. a unit test, or a caller that only cares
    about persistence). This is what `api.turn_driver._persist_answer` reads to build the
    `WebSearchSourcesBlock` that survives past the live stream."""
    sources = (
        WebSearchSource(url="https://example.com/a", title="Source A"),
        WebSearchSource(url="https://example.com/b", title="Source B"),
    )
    llm = StubLLMClient(
        script=[make_text_response("Here is some context.")],
        web_search_sources=[sources],
    )
    loop = AgentLoop(llm, tools=[], max_steps=2)

    result = await loop.run_turn("what's the latest on 2330?", tool_context=ToolContext())

    assert result.web_search_sources == sources


# ---- a model's bad tool arguments must be fed back to it, never kill the turn ----


async def test_invalid_tool_arguments_become_a_tool_error_not_a_crashed_turn() -> None:
    llm = StubLLMClient(
        script=[
            make_tool_call_response(tool_call_id="call-1", name="echo_tool", arguments={}),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4)

    result = await loop.run_turn("call the tool wrongly", tool_context=ToolContext())

    tool_result = result.steps[0].tool_messages[0].tool_results[0]
    assert tool_result.is_error is True
    assert "invalid arguments for echo_tool" in tool_result.content
    assert "text" in tool_result.content  # names the offending field
    assert result.final_text  # the turn ran to completion instead of raising


async def test_the_model_can_correct_itself_after_an_argument_error() -> None:
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": ""}
            ),
            make_tool_call_response(
                tool_call_id="call-2", name="echo_tool", arguments={"text": "second try"}
            ),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=5)

    result = await loop.run_turn("retry after a bad call", tool_context=ToolContext())

    assert result.steps[0].tool_messages[0].tool_results[0].is_error is True
    assert result.steps[1].tool_messages[0].tool_results[0].is_error is False


async def test_the_feedback_lists_valid_options_but_never_echoes_the_rejected_value() -> None:
    # The failure seen live: a model put an out-of-set word in an enum field.
    secret_looking = "close-SENTINEL-value"
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1",
                name="propose_alert",
                arguments={
                    "symbol": "2330",
                    "market": secret_looking,
                    "condition": {"kind": "close_price", "comparator": "above", "value": 800},
                },
            ),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[ProposeAlertTool()], max_steps=4)

    result = await loop.run_turn("alert me", tool_context=ToolContext())

    content = result.steps[0].tool_messages[0].tool_results[0].content
    assert "market" in content
    assert "TW" in content  # tells the model what IS valid
    assert secret_looking not in content  # ...without reflecting what it sent


async def test_a_recovered_propose_alert_call_still_produces_its_card() -> None:
    good = {
        "symbol": "2330",
        "market": "TW",
        "condition": {"kind": "close_price", "comparator": "above", "value": 800},
    }
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1",
                name="propose_alert",
                arguments={**good, "trigger_mode": "close"},
            ),
            make_tool_call_response(tool_call_id="call-2", name="propose_alert", arguments=good),
            make_text_response("已列出提醒提案，請確認。"),
        ]
    )
    loop = AgentLoop(llm, tools=[ProposeAlertTool()], max_steps=5)

    result = await loop.run_turn("2330 收盤價高於 800 提醒我", tool_context=ToolContext())

    assert [c.card_type for c in result.rich_cards] == ["alert_proposal"]
    assert result.rich_cards[0].data["condition"]["value"] == 800  # type: ignore[index]


async def test_hide_cards_for_drops_the_card_but_the_model_still_reads_the_data() -> None:
    """A report skill fetches identity data only to write about it — its raw card is clutter above
    the charts. The card is dropped; the tool result the model sees is untouched."""
    arguments = {
        "symbol": "2330",
        "market": "TW",
        "condition": {"kind": "close_price", "comparator": "above", "value": 800},
    }

    def script() -> StubLLMClient:
        return StubLLMClient(
            script=[
                make_tool_call_response(
                    tool_call_id="c1", name="propose_alert", arguments=arguments
                ),
                make_text_response("done"),
            ]
        )

    shown = await AgentLoop(script(), tools=[ProposeAlertTool()], max_steps=4).run_turn(
        "x", tool_context=ToolContext()
    )
    assert any(card.card_type == "alert_proposal" for card in shown.rich_cards)

    hidden = await AgentLoop(
        script(),
        tools=[ProposeAlertTool()],
        max_steps=4,
        hide_cards_for=frozenset({"propose_alert"}),
    ).run_turn("x", tool_context=ToolContext())
    assert hidden.rich_cards == ()
    tool_text = hidden.steps[0].tool_messages[0].tool_results[0].content
    assert "2330" in tool_text  # the model still got the data


async def test_a_tool_call_written_out_as_the_reply_is_run_as_the_call_it_was_meant_to_be() -> None:
    """Gemini sometimes answers with the call as plain JSON instead of calling it; the loop runs it
    as a tool call and the raw JSON never becomes the answer."""
    llm = StubLLMClient(
        script=[
            make_text_response('{"name": "echo_tool", "arguments": {"text": "hello"}}'),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4)

    result = await loop.run_turn("say hello", tool_context=ToolContext())

    assert [call.tool_name for call in result.tool_calls] == ["echo_tool"]
    assert result.final_text.startswith("done")
    assert "echo_tool" not in result.final_text


async def test_json_that_is_not_a_known_tool_stays_an_ordinary_reply() -> None:
    reply = '{"name": "not_a_tool", "arguments": {}}'
    llm = StubLLMClient(script=[make_text_response(reply)])
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4)

    result = await loop.run_turn("hi", tool_context=ToolContext())

    assert result.tool_calls == ()
    assert "not_a_tool" in result.final_text
