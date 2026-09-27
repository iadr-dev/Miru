"""The US-tab ticker-strip aggregator tool — S&P500/Dow Jones/Nasdaq/SOX/VIX/US 10Y yield plus
the Fed funds rate in one public bundle, the US-market counterpart to
`market_index_strip_tools.py`'s TW-scoped 加權指數/櫃買指數/美元/日圓/黃金/WTI/Brent strip.

Confirmed live this session: `^GSPC`/`^DJI`/`^IXIC`/`^SOX`/`^VIX`/`^TNX` all return real
`fast_info.lastPrice`/`previousClose` quotes via `sources.yfinance.fetch_index_quote` (a live,
bounded, per-symbol fetch — these index tickers are never part of our own ingested
`us_price_eod` equity universe, so there is no whole-market-ingested path for them, the same
accepted live-fetch shape `get_symbol_search`/`get_calendar_events_feed` already establish).
`central_bank_rate` (Fed funds rate) is the one item reused from the already-router-backed
`get_macro_indicators` bundle, filtered to the `FED` row — real, whole-market-ingested data, not
a live fetch.
"""

from __future__ import annotations

from datetime import UTC
from datetime import datetime as datetime_

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.index_quote import fetch_index_quote
from dataaccess.router import DataAccessRouter
from schemas.capabilities import CentralBankRateRow, MarketIndexStripItemRow
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from tools._rows import fetch_rows
from tools.base import BaseTool, Tool, ToolContext

_FED_COUNTRY_CODE = "FED"

# `(key, label, yahoo_symbol)` — one live index-quote item each.
_INDEX_ITEMS: tuple[tuple[str, str, str], ...] = (
    ("sp500", "S&P 500", "^GSPC"),
    ("dow_jones", "Dow Jones", "^DJI"),
    ("nasdaq", "Nasdaq", "^IXIC"),
    ("sox", "費城半導體指數", "^SOX"),
    ("vix", "VIX", "^VIX"),
    ("us10y", "美債10年殖利率", "^TNX"),
)


class GetUsMarketIndexStripInput(BaseModel):
    """No fields — every underlying item here is a market-level panel with no per-symbol scope
    a caller could narrow (mirrors `GetMarketIndexStripInput`'s own reasoning)."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class GetUsMarketIndexStripOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rows: tuple[MarketIndexStripItemRow, ...] = Field(default_factory=tuple)


def _change(latest: float, prior: float | None) -> tuple[float | None, float | None]:
    """`(change, change_pct)` against `prior`, or `(None, None)` when there is no prior reading
    to diff against — never a fabricated zero. Mirrors `market_index_strip_tools.py`'s own
    identical helper (duplicated narrowly rather than shared, since importing across the two
    otherwise-independent tool modules for one 6-line helper would be more indirection than the
    duplication itself)."""
    change = latest - prior if prior is not None else None
    change_pct = (change / prior * 100) if change is not None and prior else None
    return change, change_pct


class GetUsMarketIndexStripTool(BaseTool):
    """`get_us_market_index_strip` — bundles S&P500/Dow Jones/Nasdaq/SOX/VIX/US 10Y yield (live
    yfinance index quotes) plus Fed funds rate (`central_bank_rate`, router-backed) into one
    public ticker-strip report. Each item is fetched independently — a missing/failed fetch for
    one item simply omits that item from `rows` rather than fabricating a value, so a single
    upstream hiccup never blanks the whole strip."""

    name = "get_us_market_index_strip"
    description = (
        "Get the US-tab ticker-strip items (S&P500/Dow Jones/Nasdaq/SOX/VIX/US 10Y yield/Fed "
        "funds rate) — current value plus change vs. the prior close, for each item "
        "independently."
    )
    input_model: type[BaseModel] = GetUsMarketIndexStripInput
    output_model: type[BaseModel] = GetUsMarketIndexStripOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "us_market_index_strip"

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del args
        rows: list[MarketIndexStripItemRow] = []
        today = datetime_.now(UTC).date()

        for key, label, yahoo_symbol in _INDEX_ITEMS:
            last_price, previous_close = await fetch_index_quote(yahoo_symbol)
            if last_price is None:
                continue
            change, change_pct = _change(last_price, previous_close)
            rows.append(
                MarketIndexStripItemRow(
                    key=key,
                    label=label,
                    value=last_price,
                    change=change,
                    change_pct=change_pct,
                    as_of=today,
                )
            )

        fed_row = await self._fed_rate_row(ctx)
        if fed_row is not None:
            rows.append(fed_row)
        return GetUsMarketIndexStripOutput(rows=tuple(rows))

    async def _fed_rate_row(self, ctx: ToolContext) -> MarketIndexStripItemRow | None:
        _rate_result, rate_rows = await fetch_rows(
            self._router,
            Capability.CENTRAL_BANK_RATE,
            Symbol(id="", market=Market.GLOBAL),
            tls_policy=ctx.tls_policy,
            model=CentralBankRateRow,
            key="rows",
        )
        fed_rows = [row for row in rate_rows if row.country == _FED_COUNTRY_CODE]
        if not fed_rows:
            return None
        latest_fed = fed_rows[-1]
        prior_fed = fed_rows[-2].interest_rate if len(fed_rows) > 1 else None
        change, change_pct = _change(latest_fed.interest_rate, prior_fed)
        return MarketIndexStripItemRow(
            key="fed_rate",
            label="Fed 利率",
            value=latest_fed.interest_rate,
            change=change,
            change_pct=change_pct,
            as_of=latest_fed.date,
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [(GetUsMarketIndexStripTool(router), False)]


__all__ = [
    "GetUsMarketIndexStripInput",
    "GetUsMarketIndexStripOutput",
    "GetUsMarketIndexStripTool",
    "build_registration_table",
]
