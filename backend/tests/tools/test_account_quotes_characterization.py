"""Characterization of `GetMyWatchlistsTool._with_quotes` (the `include_quotes` join): symbol
de-duplication across groups, the per-call cap with and without a registered quote tool, a quote
tool that refuses / returns fewer rows than asked, and per-market keying. Synthetic data only."""

from __future__ import annotations

from pydantic import BaseModel

from schemas.enums import DataClass, Market, Tier
from tests.tools.test_account_tools import (
    ALICE,
    FakeReader,
    StubQuotesTool,
    _group,
    _tools,
)
from tools.account_tools import (
    MAX_QUOTED_SYMBOLS,
    GetMyWatchlistsInput,
    GetMyWatchlistsOutput,
    WatchlistsSnapshot,
)
from tools.base import ToolContext
from tools.market.symbols import GetSymbolQuotesInput, GetSymbolQuotesOutput


class _PartialQuotesTool(StubQuotesTool):
    """Answers only symbols that start with '2' (the rest are simply absent from the reply)."""

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        full = await super().run(args, ctx)
        assert isinstance(full, GetSymbolQuotesOutput)
        return full.model_copy(update={"rows": tuple(r for r in full.rows if r.symbol[0] == "2")})


class _SponsorQuotesTool(StubQuotesTool):
    """A quote tool the FREE caller is not entitled to, so dispatch returns a non-output."""

    data_class = DataClass.SPONSOR


async def _run(snapshot: WatchlistsSnapshot, quotes: StubQuotesTool | None) -> object:
    return await _tools(FakeReader(watchlists=snapshot), quotes)["get_my_watchlists"].run(
        GetMyWatchlistsInput(include_quotes=True), ALICE
    )


async def test_a_symbol_in_two_groups_is_requested_once_and_priced_in_both() -> None:
    quotes = StubQuotesTool()
    snapshot = WatchlistsSnapshot(
        groups=(_group("a", ["2330", "2317"]), _group("b", ["2317", "2454"])), total_items=4
    )

    out = await _run(snapshot, quotes)

    assert isinstance(out, GetMyWatchlistsOutput)
    assert len(quotes.requests) == 1
    assert quotes.requests[0].symbols == ("2330", "2317", "2454")
    assert quotes.requests[0].days == 2
    assert out.quotes_truncated is False
    assert [e.close for e in out.groups[0].items] == [100.0, 100.0]
    assert [e.close for e in out.groups[1].items] == [100.0, 100.0]
    assert [g.name for g in out.groups] == ["a", "b"]


async def test_rows_the_quote_tool_did_not_return_stay_without_figures() -> None:
    quotes = _PartialQuotesTool()
    snapshot = WatchlistsSnapshot(groups=(_group("g", ["2330", "AAPL"]),), total_items=2)

    out = await _run(snapshot, quotes)

    assert isinstance(out, GetMyWatchlistsOutput)
    priced, unpriced = out.groups[0].items
    assert (priced.symbol, priced.close, priced.name) == ("2330", 100.0, "name-2330")
    assert (unpriced.symbol, unpriced.close, unpriced.change_pct, unpriced.name) == (
        "AAPL",
        None,
        None,
        None,
    )


async def test_a_refused_quote_dispatch_leaves_the_rows_untouched() -> None:
    quotes = _SponsorQuotesTool()
    snapshot = WatchlistsSnapshot(groups=(_group("g", ["2330"]),), total_items=1)
    ctx_free = ToolContext(user_id="alice", tier=Tier.FREE)

    out = await _tools(FakeReader(watchlists=snapshot), quotes)["get_my_watchlists"].run(
        GetMyWatchlistsInput(include_quotes=True), ctx_free
    )

    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.quotes_included is True
    assert out.quotes_truncated is False
    assert out.groups[0].items[0].close is None
    assert out.groups[0].items[0].name is None


async def test_the_cap_still_flags_truncation_when_no_quote_tool_is_registered() -> None:
    symbols = [str(1000 + i) for i in range(MAX_QUOTED_SYMBOLS + 5)]
    snapshot = WatchlistsSnapshot(groups=(_group("big", symbols),), total_items=len(symbols))

    out = await _run(snapshot, None)

    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.quotes_truncated is True
    assert all(e.close is None for e in out.groups[0].items)
    assert len(out.groups[0].items) == len(symbols)


async def test_the_same_symbol_in_two_markets_is_requested_and_priced_per_market() -> None:
    quotes = StubQuotesTool()
    snapshot = WatchlistsSnapshot(
        groups=(_group("tw", ["600"]), _group("us", ["600"], Market.US)), total_items=2
    )

    out = await _run(snapshot, quotes)

    assert isinstance(out, GetMyWatchlistsOutput)
    assert {(r.market, r.symbols) for r in quotes.requests} == {
        (Market.TW, ("600",)),
        (Market.US, ("600",)),
    }
    assert all(r.market in (Market.TW, Market.US) for r in quotes.requests)
    assert isinstance(quotes.requests[0], GetSymbolQuotesInput)
    assert out.groups[0].items[0].close == 100.0
    assert out.groups[1].items[0].close == 100.0


async def test_the_cap_counts_distinct_symbols_across_markets_in_encounter_order() -> None:
    quotes = StubQuotesTool()
    tw = [str(1000 + i) for i in range(MAX_QUOTED_SYMBOLS - 1)]
    snapshot = WatchlistsSnapshot(
        groups=(_group("tw", tw), _group("us", ["AAPL", "MSFT"], Market.US)),
        total_items=MAX_QUOTED_SYMBOLS + 1,
    )

    out = await _run(snapshot, quotes)

    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.quotes_truncated is True
    us_requests = [r for r in quotes.requests if r.market is Market.US]
    assert [r.symbols for r in us_requests] == [("AAPL",)]  # MSFT is the one over the cap
    us_items = out.groups[1].items
    assert (us_items[0].close, us_items[1].close) == (100.0, None)
