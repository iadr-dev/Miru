"""Tool registry wiring — assembles this phase's built tool groups into one `ToolRegistry`.

`build_default_tool_registry(router)` is the single call site the future agent runtime
(Phase 6+) and this phase's own tests use to get a fully-registered registry — it composes
each group module's own `build_registration_table(router)` via `registry.build_default_registry`,
so adding a new tool group later is a one-line addition here, not a re-derivation of the
immediate/deferred split.

`web_search_tools` is the one group here not parameterized by `router` (its `WebSearchGateway`
seam bypasses the router entirely — see that module's docstring), so it takes no argument.
`theme_tools` is the one group parameterized by the DB session factory rather than the router: it
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
from tools import (
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
    macro_tools,
    market_index_strip_tools,
    market_tools,
    mops_tools,
    others_tools,
    realtime_tools,
    score_tools,
    screener_tools,
    tdcc_tools,
    technical_tools,
    theme_tools,
    web_search_tools,
)
from tools.registry import ToolRegistry, build_default_registry


def build_default_tool_registry(
    router: DataAccessRouter,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    market_data_reader: ParquetMarketDataReader | None = None,
) -> ToolRegistry:
    theme_group = (
        theme_tools.build_registration_table(session_factory) if session_factory is not None else []
    )
    score_group = (
        score_tools.build_registration_table(session_factory) if session_factory is not None else []
    )
    # The tag screener reads the ingest-owned Parquet flag store through the derived-store reader.
    # Optional so a narrow test wiring only the router-backed set still composes; when omitted, the
    # screener group is simply not registered (its catalog tool goes with it — they are a pair).
    screener_group = (
        screener_tools.build_registration_table(market_data_reader)
        if market_data_reader is not None
        else []
    )
    return build_default_registry(
        ask_user_tools.build_registration_table(),
        context_tools.build_registration_table(router),
        health_tools.build_registration_table(router),
        market_tools.build_registration_table(router),
        market_index_strip_tools.build_registration_table(router),
        mops_tools.build_registration_table(router),
        disposition_board_tools.build_registration_table(router),
        calendar_tools.build_registration_table(router),
        live_tools.build_registration_table(router),
        gov_api_tools.build_registration_table(router),
        etf_pcf_tools.build_registration_table(router),
        etf_overlap_tools.build_registration_table(router),
        web_search_tools.build_registration_table(),
        analyst_tools.build_registration_table(router),
        chip_tools.build_registration_table(router),
        technical_tools.build_registration_table(router),
        fundamental_tools.build_registration_table(router),
        derivatives_tools.build_registration_table(router),
        convertible_bond_tools.build_registration_table(router),
        macro_tools.build_registration_table(router),
        international_tools.build_registration_table(router),
        others_tools.build_registration_table(router),
        realtime_tools.build_registration_table(router),
        tdcc_tools.build_registration_table(router),
        theme_group,
        score_group,
        screener_group,
    )


__all__ = ["build_default_tool_registry"]
