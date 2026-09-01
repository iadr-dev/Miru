"""The `YFinanceAdapter` class — dispatch skeleton only.

Composition choice: each capability domain module (`analyst.py`, `price_financials.py`,
`holders.py`, `discovery.py`) exports plain functions that take the already-fetched frame
bundle (and any other needed arguments) explicitly, rather than being written as methods on
a mixin class. `YFinanceAdapter` imports those functions and wires them into two small
dispatch tables (`_dispatch_no_bundle_capability`'s handler dict and
`_dispatch_frame_capability`'s handler dict). This was chosen over a mixin-class split
because none of the domain handlers touch adapter instance state — they're pure functions of
their arguments (the FinMind sibling split's category modules follow the identical shape,
returning plain `DATASETS` dicts merged by a registry rather than mixin classes) — a mixin
would only add inheritance-graph complexity without buying anything, since there's no shared
mutable state to protect. Adding a new capability domain later means: add a domain module
with plain functions, then add its capability(ies) to one of the two dispatch tables here —
no class hierarchy to extend.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, cast

import anyio.to_thread
import structlog

from schemas.enums import Capability, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult

from ._blocking import _fetch_info_blocking, _fetch_ticker_frames_blocking
from ._shared import _NON_TW_MARKETS, _NON_TW_SUPPORTED, _TW_SUPPORTED, _result, _yahoo_symbol
from .analyst import _analyst_consensus, _esg, _etf_aum, _sector_class
from .discovery import (
    _earnings_calendar,
    _economic_calendar,
    _industry_overview,
    _ipo_calendar,
    _market_status,
    _sector_overview,
    _symbol_lookup,
    _symbol_search,
)
from .holders import _fund_holdings, _insider_activity, _institutional_holders, _options_chain
from .price_financials import _dividends, _financial_statement, _price_eod, _split_price

logger = structlog.get_logger(__name__)


class YFinanceAdapter:
    """The `YFINANCE` source adapter."""

    source_id = "YFINANCE"
    timing_class = TimingClass.REFERENCE

    def supports(self) -> set[tuple[Capability, Market]]:
        pairs = {(cap, Market.TW) for cap in _TW_SUPPORTED}
        pairs |= {(cap, market) for cap in _NON_TW_SUPPORTED for market in _NON_TW_MARKETS}
        return pairs

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        """Contract is trivially satisfied by the type signature; kept for Protocol parity
        with other adapters — yfinance's frame-returning properties always return
        dict-shaped/DataFrame-shaped objects, never raise on a well-formed response."""
        return raw

    # Capabilities dispatched here never fetch a per-symbol `Ticker` frame bundle — each has
    # its own dedicated blocking call (market-wide search/lookup/calendars/sector/industry/
    # market-status), so `fetch()` can route to them before ever touching `_yahoo_symbol`.
    _NO_TICKER_BUNDLE: ClassVar[set[Capability]] = {
        Capability.SYMBOL_SEARCH,
        Capability.SYMBOL_LOOKUP,
        Capability.EARNINGS_CALENDAR,
        Capability.IPO_CALENDAR,
        Capability.ECONOMIC_CALENDAR,
        Capability.SECTOR_OVERVIEW,
        Capability.INDUSTRY_OVERVIEW,
        Capability.MARKET_STATUS,
    }

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy,
        # yfinance manages its own HTTP session internally (a `requests`-based session, never
        # `httpx`) — the codebase's shared TLS-client-factory seam is never constructed here;
        # there is no seam for `tls_policy` to apply to inside this adapter.
        **params: object,
    ) -> SourceResult:
        supported = _TW_SUPPORTED if sym.market is Market.TW else _NON_TW_SUPPORTED
        if cap not in supported or (
            sym.market is not Market.TW and sym.market not in _NON_TW_MARKETS
        ):
            return _result(
                payload={},
                health=SourceHealth(
                    status=HealthStatus.UNAVAILABLE_TIER,
                    detail=f"YFINANCE does not serve {cap.value} for {sym.market.value}",
                ),
            )

        if cap in self._NO_TICKER_BUNDLE:
            return await self._dispatch_no_bundle_capability(cap, sym, params)

        yahoo_symbol = _yahoo_symbol(sym)
        try:
            frames = await self._fetch_frames(cap, sym, yahoo_symbol)
        except Exception as exc:
            # M5Q rather than propagating — never let a yfinance outage break a core flow.
            logger.warning("yfinance_fetch_failed", symbol=yahoo_symbol, error=str(exc))
            return _result(
                payload={},
                health=SourceHealth(status=HealthStatus.UNREACHABLE, detail=str(exc)),
            )

        if cap is Capability.OPTIONS_CHAIN:
            return await _options_chain(sym, yahoo_symbol, frames, params)
        return self._dispatch_frame_capability(cap, sym, frames)

    async def _dispatch_no_bundle_capability(
        self, cap: Capability, sym: Symbol, params: dict[str, object]
    ) -> SourceResult:
        # `params` is unused by the sym-only handlers — one uniform signature keeps this a
        # plain lookup table instead of a long if/elif chain.
        handlers: dict[
            Capability, Callable[[Symbol, dict[str, object]], Awaitable[SourceResult]]
        ] = {
            Capability.SYMBOL_SEARCH: _symbol_search,
            Capability.SYMBOL_LOOKUP: _symbol_lookup,
            Capability.EARNINGS_CALENDAR: lambda s, _p: _earnings_calendar(s),
            Capability.IPO_CALENDAR: lambda s, _p: _ipo_calendar(s),
            Capability.ECONOMIC_CALENDAR: lambda s, _p: _economic_calendar(s),
            Capability.SECTOR_OVERVIEW: _sector_overview,
            Capability.INDUSTRY_OVERVIEW: _industry_overview,
            Capability.MARKET_STATUS: lambda s, _p: _market_status(s),
        }
        return await handlers[cap](sym, params)

    async def _fetch_frames(
        self, cap: Capability, sym: Symbol, yahoo_symbol: str
    ) -> dict[str, Any]:
        if (
            cap
            in (
                Capability.ANALYST_CONSENSUS,
                Capability.ESG,
                Capability.SECTOR_CLASS,
                Capability.ETF_AUM,
            )
            and sym.market is Market.TW
        ):
            # TW's original narrow path only ever needed `.info` — kept as its own
            # lighter-weight fetch rather than the full non-TW frame bundle.
            info = await anyio.to_thread.run_sync(_fetch_info_blocking, yahoo_symbol)
            return {"info": info}
        return await anyio.to_thread.run_sync(_fetch_ticker_frames_blocking, yahoo_symbol)

    def _dispatch_frame_capability(
        self, cap: Capability, sym: Symbol, frames: dict[str, Any]
    ) -> SourceResult:
        info = cast("dict[str, Any]", frames.get("info") or {})
        handlers: dict[Capability, Callable[[], SourceResult]] = {
            Capability.ANALYST_CONSENSUS: lambda: _analyst_consensus(sym, info, frames),
            Capability.ESG: lambda: _esg(sym, info),
            Capability.SECTOR_CLASS: lambda: _sector_class(sym, info),
            Capability.ETF_AUM: lambda: _etf_aum(sym, info),
            Capability.PRICE_EOD: lambda: _price_eod(sym, frames),
            Capability.FINANCIALS: lambda: _financial_statement(
                sym, frames, "financials", "income_statement"
            ),
            Capability.BALANCE_SHEET: lambda: _financial_statement(
                sym, frames, "balance_sheet", "balance_sheet"
            ),
            Capability.CASH_FLOW_STATEMENT: lambda: _financial_statement(
                sym, frames, "cash_flow", "cash_flow"
            ),
            Capability.DIVIDENDS: lambda: _dividends(sym, frames),
            Capability.SPLIT_PRICE: lambda: _split_price(sym, frames),
            Capability.INSTITUTIONAL_HOLDERS: lambda: _institutional_holders(sym, frames),
            Capability.INSIDER_ACTIVITY: lambda: _insider_activity(sym, frames),
            Capability.FUND_HOLDINGS: lambda: _fund_holdings(sym, frames),
        }
        return handlers[cap]()


__all__ = ["YFinanceAdapter"]
