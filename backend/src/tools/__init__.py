"""Tool registry wiring — assembles this phase's built tool groups into one `ToolRegistry`.

`build_default_tool_registry(router)` is the single call site the future agent runtime
(Phase 6+) and this phase's own tests use to get a fully-registered registry — it composes
each group module's own `build_registration_table(router)` via `registry.build_default_registry`,
so adding a new tool group later is a one-line addition here, not a re-derivation of the
immediate/deferred split.

`web_search_tools`/`news_tools`/`ptt_tools` are the groups here not parameterized by `router`
(their `WebSearchGateway`/`NewsAdapter`/`PttAdapter` seams bypass the router entirely — see each
module's own docstring), so they take no argument. `theme_tools` is the one group parameterized
by the DB session factory rather than the router: it
reads the already-ingested relational theme store, not a live source. The session factory is
optional so a caller wiring only the router-backed set (e.g. a narrow test) still works; when it
is omitted, the theme group is simply not registered. `score_tools` shares this same shape — it
reads the relational `stock_scores` cache the daily scoring batch writes — and is likewise
registered only when a session factory is supplied.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dataaccess.router import DataAccessRouter
from ingest.reader import ParquetMarketDataReader
from ingest.store import ParquetStore
from llm.base import LLMClient
from tools import (
    account_tools,
    analyst_tools,
    ask_user_tools,
    calendar_tools,
    chip_tools,
    context_tools,
    convertible_bond_tools,
    derivatives_tools,
    disposition_board_tools,
    etf_overlap_tools,
    etf_pcf_tools,
    fundamental_tools,
    gov_api_tools,
    health_tools,
    international_tools,
    live_tools,
    macro_calendar_tools,
    macro_tools,
    market,
    market_index_strip_tools,
    market_ranking_tools,
    mops_tools,
    morning_brief_tools,
    news_tools,
    others_tools,
    propose_alert_tools,
    propose_memory_tools,
    propose_schedule_tools,
    propose_watchlist_tools,
    ptt_tools,
    realtime_tools,
    report_tools,
    score_tools,
    screener_tools,
    summary_tools,
    tdcc_tools,
    technical_tools,
    theme_tools,
    trend_score_tools,
    tw_ranking_tools,
    us_macro_tools,
    us_market_index_strip_tools,
    web_search_tools,
)
from tools.registry import ToolRegistry, build_default_registry


def build_default_tool_registry(  # noqa: PLR0913 — one keyword per optional tool-group seam
    router: DataAccessRouter,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    market_data_reader: ParquetMarketDataReader | None = None,
    summary_llm: LLMClient | None = None,
    parquet_store: ParquetStore | None = None,
    account_reader: account_tools.AccountDataReader | None = None,
) -> ToolRegistry:
    theme_group = (
        theme_tools.build_registration_table(session_factory) if session_factory is not None else []
    )
    score_group = (
        score_tools.build_registration_table(session_factory) if session_factory is not None else []
    )
    # The trend-score tool needs BOTH the DB session factory (trend_scores cache) and the router
    # (the live per-symbol broker_concentration fetch) — same optional-dependency shape as
    # `score_group`/`summary_group` so a narrow test wiring only one seam still composes.
    trend_score_group = (
        trend_score_tools.build_registration_table(session_factory, router)
        if session_factory is not None
        else []
    )
    # The AI-summary tool needs BOTH the DB session factory (score + summary cache) and a
    # resolved `LLMClient` (ModelRole.SUBAGENT, resolved by the composition root) — registered
    # only when both are supplied, matching `theme_group`/`score_group`'s optional-dependency
    # shape so a narrow test wiring only the router-backed set still composes.
    summary_group = (
        summary_tools.build_registration_table(session_factory, summary_llm)
        if session_factory is not None and summary_llm is not None
        else []
    )
    # The tag screener reads the ingest-owned Parquet flag store through the derived-store reader.
    # Optional so a narrow test wiring only the router-backed set still composes; when omitted, the
    # screener group is simply not registered (its catalog tool goes with it — they are a pair).
    # `run_screener` additionally needs the Parquet store (gates + ETF numeric reads) and the DB
    # session factory (TW `stock_scores` numeric reads) — see `screener_tools.
    # build_registration_table`'s own docstring for why it degrades gracefully without them.
    screener_group = (
        screener_tools.build_registration_table(
            market_data_reader,
            parquet_store=parquet_store,
            session_factory=session_factory,
        )
        if market_data_reader is not None
        else []
    )
    registry = build_default_registry(
        ask_user_tools.build_registration_table(),
        propose_alert_tools.build_registration_table(),
        propose_memory_tools.build_registration_table(),
        propose_watchlist_tools.build_registration_table(),
        propose_schedule_tools.build_registration_table(),
        context_tools.build_registration_table(router),
        health_tools.build_registration_table(router),
        market.build_registration_table(router),
        market_index_strip_tools.build_registration_table(router),
        us_market_index_strip_tools.build_registration_table(router),
        market_ranking_tools.build_registration_table(),
        mops_tools.build_registration_table(router),
        disposition_board_tools.build_registration_table(router),
        calendar_tools.build_registration_table(router),
        live_tools.build_registration_table(router),
        gov_api_tools.build_registration_table(router),
        etf_pcf_tools.build_registration_table(router),
        etf_overlap_tools.build_registration_table(router),
        web_search_tools.build_registration_table(),
        news_tools.build_registration_table(),
        ptt_tools.build_registration_table(),
        analyst_tools.build_registration_table(router),
        chip_tools.build_registration_table(router),
        technical_tools.build_registration_table(router),
        fundamental_tools.build_registration_table(router),
        report_tools.build_registration_table(router),
        derivatives_tools.build_registration_table(router),
        convertible_bond_tools.build_registration_table(router),
        macro_tools.build_registration_table(router),
        macro_calendar_tools.build_registration_table(),
        us_macro_tools.build_registration_table(parquet_store),
        international_tools.build_registration_table(router),
        others_tools.build_registration_table(router),
        realtime_tools.build_registration_table(router),
        tdcc_tools.build_registration_table(router),
        tw_ranking_tools.build_registration_table(),
        theme_group,
        score_group,
        screener_group,
        summary_group,
        trend_score_group,
    )
    # The morning brief composes other tools, so it is registered last, with the registry itself.
    registry.register(morning_brief_tools.build_registration(registry, router), immediate=False)
    # The user-scoped (`private`) account tools need the user-data reader, which only a composition
    # root above `tools/` can build; without one they are simply not registered (a narrow test).
    # `get_my_watchlists` reaches `get_symbol_quotes` through the registry, so these come last.
    if account_reader is not None:
        for account_tool in account_tools.build_account_tools(account_reader, registry):
            registry.register(account_tool, immediate=False)
    return registry


__all__ = ["build_default_tool_registry"]
