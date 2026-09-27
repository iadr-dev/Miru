"""Registry composition + `to_tool_spec()` round-trip + full-market-scan guard, over the
actual set of tools built this phase (via `tools.build_default_tool_registry`)."""

from __future__ import annotations

from datetime import UTC, datetime

from llm.base import ToolSpec
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


def _router():
    symbol = Symbol(id="2330", market=Market.TW)
    result = SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    router, _ = make_router(symbol, dict.fromkeys(Capability, result))
    return router


def test_immediate_core_matches_the_documented_named_subset_of_built_tools() -> None:
    registry = build_default_tool_registry(_router())
    immediate_names = {t.name for t in registry.immediate_tools()}

    # The documented named immediate-core set, restricted to what this phase actually built.
    assert immediate_names == {
        "get_canonical_context",
        "build_conversation_package",
        "get_source_health",
        "get_company_profile",
        "get_price_quote",
    }


def test_deferred_index_lists_the_remaining_real_tools() -> None:
    """The deferred long tail grew substantially across this registry's FinMind/gov-API/
    yfinance/web-search coverage expansion — this asserts the total count and a representative
    sample from each newly-added tool group, rather than re-enumerating all ~80 names (which
    would just duplicate every `build_registration_table` list and add no real coverage)."""
    registry = build_default_tool_registry(_router())
    deferred_names = {e.name for e in registry.deferred_index()}

    # `_router()` passes no `session_factory`/`market_data_reader`, so `theme_tools`/
    # `score_tools`/`screener_tools` (which require one) are never registered here — this
    # count/sample covers only the router-backed groups this fixture actually builds.
    assert len(deferred_names) == 141
    # The pre-existing core, still deferred:
    assert {
        "run_bounded_observation",
        "get_source_matrix",
        "get_dividend_schedule",
        "get_institutional_flow",
        "get_chip_flows",
        "get_active_etf_report",
        "get_etf_money_flow",
        "get_etfs_holding_stock",
        "get_daily_signal",
        "get_dividend_calendar",
    } <= deferred_names
    # One representative tool from each newly-added group:
    assert {
        "get_material_announcements",  # mops_tools
        "get_insider_shareholding",  # mops_tools
        "get_material_announcement_feed",  # mops_tools
        "get_insider_transfer_declarations",  # mops_tools
        "get_self_assessed_earnings",  # mops_tools
        "get_calendar_events",  # calendar_tools
        "get_live_quote_snapshot",  # live_tools
        "get_derivatives_settlement",  # gov_api_tools
        "get_etf_fund_info",  # etf_pcf_tools
        "get_etf_expense_ratio",  # etf_pcf_tools
        "get_etf_overlap",  # etf_overlap_tools
        "get_fund_comparison",  # etf_overlap_tools
        "web_search",  # web_search_tools
        "fetch_url",  # web_search_tools
        "get_options_chain",  # analyst_tools
        "run_equity_screener_us",  # analyst_tools
        "get_margin_short",  # chip_tools
        "get_trading_calendar",  # technical_tools
        "get_technical_indicators",  # technical_tools
        "get_financial_statement",  # fundamental_tools
        "get_dividend_policy_summary",  # fundamental_tools
        "get_futures_options_daily",  # derivatives_tools
        "get_convertible_bond_profile",  # convertible_bond_tools
        "get_macro_indicators",  # macro_tools
        "get_international_stock",  # international_tools
        "get_active_etf_roster",  # others_tools
        "get_tdcc_securities_info",  # tdcc_tools
        "ask_user",  # ask_user_tools
        "get_advance_decline",  # market_tools
        "get_live_price_quote",  # market_tools
        "get_index_contribution",  # market_tools
        "get_sector_heatmap",  # market_tools
        "get_sector_constituents",  # market_tools
        "get_stock_heatmap",  # market_tools
        "get_sector_rotation",  # market_tools
        "get_sector_rotation_rrg",  # market_tools
        "get_price_momentum",  # market_tools
        "get_holder_accumulation",  # market_tools
        "get_symbol_info",  # market_tools
        "get_symbol_directory_search",  # market_tools
        "get_price_history",  # market_tools
        "get_etf_list",  # market_tools
        "get_tick_snapshot",  # realtime_tools
        "get_futures_snapshot",  # realtime_tools
        "get_options_snapshot",  # realtime_tools
        "get_market_index_strip",  # market_index_strip_tools
        "get_disposition_board",  # disposition_board_tools
        "get_news_feed",  # news_tools
        "get_ptt_board",  # ptt_tools
        "get_ptt_stock_search",  # ptt_tools
        "get_us_market_rankings",  # market_ranking_tools
        "get_macro_calendar",  # macro_calendar_tools
        "get_us_market_index_strip",  # us_market_index_strip_tools
    } <= deferred_names


def test_total_tool_count_reflects_the_coverage_expansion() -> None:
    """5 immediate + 136 deferred = 141, across FinMind/MOPS/TWSE-MIS/TAIFEX-OpenAPI/ETF-PCF/
    web-search/yfinance/TDCC/ask_user/market_tools/realtime_tools/market_index_strip_tools/
    disposition_board_tools/news_tools/ptt_tools/market_ranking_tools/macro_calendar_tools/
    us_market_index_strip_tools —
    `_router()` builds no `session_factory`/`market_data_reader`, so theme/score/screener tools
    (which require one) are excluded from this count."""
    registry = build_default_tool_registry(_router())

    assert len(registry.all_tools()) == 146


def test_get_tool_reaches_both_immediate_and_deferred_tools() -> None:
    registry = build_default_tool_registry(_router())

    assert registry.get_tool("get_canonical_context") is not None
    assert registry.get_tool("get_chip_flows") is not None
    assert registry.get_tool("does_not_exist") is None


def test_every_registered_tool_spec_round_trips_into_a_valid_toolspec() -> None:
    registry = build_default_tool_registry(_router())

    for tool in registry.all_tools():
        spec = tool.to_tool_spec()
        assert isinstance(spec, ToolSpec)
        assert spec.name == tool.name
        assert spec.description
        assert spec.parameters.get("type") == "object"


def test_no_tool_input_schema_can_express_a_full_market_scan() -> None:
    """Every input schema requires a specific `id`+`market` (or, for `get_source_matrix`, no
    symbol-shaped field at all) — never an unbounded 'all symbols'/array-of-symbols shape.
    A finite, explicitly-capped multi-symbol comparison (e.g. `get_fund_comparison`'s 3-10
    `symbols`) is not a full-market scan and is allowed — the invariant this guards is no
    `maxItems`-less array that could express "every symbol", not "no array field ever"."""
    registry = build_default_tool_registry(_router())

    for tool in registry.all_tools():
        schema = tool.input_model.model_json_schema()
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        # No field may accept an unbounded list of symbols/a wildcard "all" market scope.
        for name, prop in properties.items():
            if prop.get("type") == "array" and name in {"id", "symbol", "symbols"}:
                assert "maxItems" in prop, (
                    f"{tool.name}.{name} is an array field with no maxItems cap — "
                    "could express a full-market scan"
                )

        if "id" in properties:
            assert "id" in required
            assert "market" in required
