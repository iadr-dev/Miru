"""The `private` account tools (`get_my_*`): signed-in happy path, empty, anonymous refusal, the
user id coming only from the context (never from arguments), and the output bounds. The reader is
a fake that records the user id it was asked for; the real, SQL-backed one is tested in
`tests/accounts/test_account_reader.py`."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import BaseModel, ValidationError

from schemas.enums import DataClass, Market, Tier, UserMarket
from tools.account_tools import (
    DEFAULT_NOTIFICATIONS,
    MAX_NOTIFICATIONS,
    MAX_QUOTED_SYMBOLS,
    MAX_WATCHLIST_ITEMS,
    AlertRuleSummary,
    AutomationsSnapshot,
    ChatQuota,
    GetMyAutomationsInput,
    GetMyAutomationsOutput,
    GetMyNotificationsInput,
    GetMyNotificationsOutput,
    GetMyUsageInput,
    GetMyUsageOutput,
    GetMyWatchlistsInput,
    GetMyWatchlistsOutput,
    LlmUsageToday,
    NotificationSummary,
    StrategyWatchSummary,
    UsageSnapshot,
    WatchlistEntry,
    WatchlistGroupSummary,
    WatchlistsSnapshot,
    build_account_tools,
)
from tools.base import (
    PrivateToolAnonymousError,
    SignInRequired,
    Tool,
    ToolContext,
    dispatch_tool,
)
from tools.market.symbols import GetSymbolQuotesInput, GetSymbolQuotesOutput, SymbolQuoteRow
from tools.registry import ToolRegistry

ALICE = ToolContext(user_id="alice", tier=Tier.FREE)
ANONYMOUS = ToolContext(user_id=None)
NOW = datetime(2026, 10, 18, 9, 0, tzinfo=UTC)


class FakeReader:
    """Serves canned snapshots and records every user id it is asked for."""

    def __init__(
        self,
        *,
        watchlists: WatchlistsSnapshot | None = None,
        automations: AutomationsSnapshot | None = None,
        usage: UsageSnapshot | None = None,
        notifications: list[NotificationSummary] | None = None,
    ) -> None:
        self._watchlists = watchlists or WatchlistsSnapshot()
        self._automations = automations or AutomationsSnapshot()
        self._usage = usage or UsageSnapshot(chat=ChatQuota(used_today=0, limit_per_day=3))
        self._notifications = notifications or []
        self.users: list[str] = []
        self.calls: list[dict[str, object]] = []

    async def watchlists(
        self, *, user_id: str, market: UserMarket | None, max_items: int
    ) -> WatchlistsSnapshot:
        self.users.append(user_id)
        self.calls.append({"market": market, "max_items": max_items})
        return self._watchlists

    async def automations(
        self, *, user_id: str, max_alerts: int, max_schedules: int
    ) -> AutomationsSnapshot:
        self.users.append(user_id)
        return self._automations

    async def usage(self, *, user_id: str, tier: Tier, include_llm: bool) -> UsageSnapshot:
        self.users.append(user_id)
        self.calls.append({"tier": tier, "include_llm": include_llm})
        return self._usage

    async def notifications(
        self, *, user_id: str, limit: int, unread_only: bool
    ) -> list[NotificationSummary]:
        self.users.append(user_id)
        self.calls.append({"limit": limit, "unread_only": unread_only})
        return self._notifications


class StubQuotesTool:
    """Stands in for the registered `get_symbol_quotes` (the existing quote path)."""

    name = "get_symbol_quotes"
    description = "stub"
    input_model: type[BaseModel] = GetSymbolQuotesInput
    output_model: type[BaseModel] = GetSymbolQuotesOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = None
    supports_row_preview = False

    def __init__(self) -> None:
        self.requests: list[GetSymbolQuotesInput] = []

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx
        assert isinstance(args, GetSymbolQuotesInput)
        self.requests.append(args)
        return GetSymbolQuotesOutput(
            market=args.market.value,
            rows=tuple(
                SymbolQuoteRow(
                    symbol=symbol,
                    name=f"name-{symbol}",
                    trade_date=date(2026, 10, 16),
                    close=100.0,
                    change_pct=1.5,
                )
                for symbol in args.symbols
            ),
        )

    def to_tool_spec(self) -> object:  # pragma: no cover - unused by these tests
        raise NotImplementedError


def _tools(reader: FakeReader, quotes: StubQuotesTool | None = None) -> dict[str, Tool]:
    registry = ToolRegistry()
    if quotes is not None:
        registry.register(quotes)  # type: ignore[arg-type]
    return {t.name: t for t in build_account_tools(reader, registry)}


def _entry(symbol: str, market: UserMarket = Market.TW) -> WatchlistEntry:
    return WatchlistEntry(symbol=symbol, market=market)


def _group(name: str, symbols: list[str], market: UserMarket = Market.TW) -> WatchlistGroupSummary:
    return WatchlistGroupSummary(
        name=name,
        market=market,
        asset_class="stock",
        is_default=False,
        item_count=len(symbols),
        items=tuple(_entry(s, market) for s in symbols),
    )


def test_the_four_tools_are_private_and_deferred_shaped() -> None:
    tools = _tools(FakeReader())
    assert set(tools) == {
        "get_my_watchlists",
        "get_my_automations",
        "get_my_usage",
        "get_my_notifications",
    }
    assert all(t.data_class is DataClass.PRIVATE for t in tools.values())
    assert all(t.card_type is None for t in tools.values())


@pytest.mark.parametrize(
    "name", ["get_my_watchlists", "get_my_automations", "get_my_usage", "get_my_notifications"]
)
def test_no_input_can_name_a_user(name: str) -> None:
    """The user comes from the ToolContext only: no input field names one, and an unknown field is
    rejected, so a prompt-injected `user_id` argument is a bad tool argument, not a lookup."""
    model = _tools(FakeReader())[name].input_model
    assert not {"user_id", "email", "account", "owner"} & set(model.model_fields)
    for field in ("user_id", "email", "account_id"):
        with pytest.raises(ValidationError):
            model.model_validate({field: "bob"})


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("get_my_watchlists", GetMyWatchlistsInput()),
        ("get_my_automations", GetMyAutomationsInput()),
        ("get_my_usage", GetMyUsageInput()),
        ("get_my_notifications", GetMyNotificationsInput()),
    ],
)
async def test_an_anonymous_caller_gets_a_sign_in_result_and_nothing_is_read(
    name: str, args: BaseModel
) -> None:
    reader = FakeReader(watchlists=WatchlistsSnapshot(groups=(_group("g", ["2330"]),)))
    result = await dispatch_tool(_tools(reader)[name], args, ANONYMOUS)

    assert isinstance(result, SignInRequired)
    assert result.tool_name == name
    assert "Sign in" in result.message
    assert reader.users == []


async def test_running_a_private_tool_without_a_user_directly_is_a_wiring_error() -> None:
    tool = _tools(FakeReader())["get_my_usage"]
    args = GetMyUsageInput()
    with pytest.raises(PrivateToolAnonymousError):
        await tool.run(args, ANONYMOUS)


async def test_every_read_is_for_the_context_user_even_if_arguments_try_otherwise() -> None:
    reader = FakeReader()
    tools = _tools(reader)
    await dispatch_tool(tools["get_my_watchlists"], GetMyWatchlistsInput(), ALICE)
    await dispatch_tool(tools["get_my_automations"], GetMyAutomationsInput(), ALICE)
    await dispatch_tool(tools["get_my_usage"], GetMyUsageInput(), ALICE)
    await dispatch_tool(tools["get_my_notifications"], GetMyNotificationsInput(), ALICE)
    assert reader.users == ["alice"] * 4

    # What a prompt-injected call looks like: it never gets as far as the reader.
    with pytest.raises(ValidationError):
        tools["get_my_notifications"].input_model.model_validate({"user_id": "bob", "limit": 5})
    assert "bob" not in reader.users


# ---- get_my_watchlists -----------------------------------------------------------------------


async def test_watchlists_happy_path() -> None:
    snapshot = WatchlistsSnapshot(
        groups=(_group("半導體", ["2330", "2454"]), _group("美股", ["AAPL"], Market.US)),
        total_items=3,
    )
    out = await _tools(FakeReader(watchlists=snapshot))["get_my_watchlists"].run(
        GetMyWatchlistsInput(), ALICE
    )
    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.signed_in is True
    assert [g.name for g in out.groups] == ["半導體", "美股"]
    assert out.total_items == 3
    assert not out.truncated
    assert not out.quotes_included
    assert all(e.close is None for g in out.groups for e in g.items)
    assert out.currentness_caveat


async def test_watchlists_empty() -> None:
    out = await _tools(FakeReader())["get_my_watchlists"].run(GetMyWatchlistsInput(), ALICE)
    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.groups == ()
    assert out.total_items == 0
    # An empty list is stated as a normal state (starter lists are made when the page is first
    # opened), never left for the model to read as a failure.
    assert out.note is not None
    assert "No watchlists yet" in out.note
    assert "not an error" in out.note


async def test_watchlists_passes_the_item_cap_and_market_filter_to_the_reader() -> None:
    reader = FakeReader()
    await _tools(reader)["get_my_watchlists"].run(GetMyWatchlistsInput(market=Market.US), ALICE)
    assert reader.calls == [{"market": Market.US, "max_items": MAX_WATCHLIST_ITEMS}]
    assert MAX_WATCHLIST_ITEMS == 200


async def test_watchlists_surface_truncation_from_the_reader() -> None:
    snapshot = WatchlistsSnapshot(groups=(_group("g", ["2330"]),), total_items=450, truncated=True)
    out = await _tools(FakeReader(watchlists=snapshot))["get_my_watchlists"].run(
        GetMyWatchlistsInput(), ALICE
    )
    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.truncated is True
    assert out.total_items == 450


async def test_include_quotes_joins_the_existing_quote_tool() -> None:
    quotes = StubQuotesTool()
    snapshot = WatchlistsSnapshot(
        groups=(_group("tw", ["2330", "2317"]), _group("us", ["AAPL"], Market.US)),
        total_items=3,
    )
    out = await _tools(FakeReader(watchlists=snapshot), quotes)["get_my_watchlists"].run(
        GetMyWatchlistsInput(include_quotes=True), ALICE
    )
    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.quotes_included
    assert not out.quotes_truncated
    by_symbol = {e.symbol: e for g in out.groups for e in g.items}
    assert by_symbol["2330"].close == 100.0
    assert by_symbol["2330"].change_pct == 1.5
    assert by_symbol["AAPL"].name == "name-AAPL"
    assert {(r.market, r.symbols) for r in quotes.requests} == {
        (Market.TW, ("2330", "2317")),
        (Market.US, ("AAPL",)),
    }


async def test_include_quotes_is_bounded_and_batched() -> None:
    quotes = StubQuotesTool()
    symbols = [str(1000 + i) for i in range(150)]
    snapshot = WatchlistsSnapshot(groups=(_group("big", symbols),), total_items=150)
    out = await _tools(FakeReader(watchlists=snapshot), quotes)["get_my_watchlists"].run(
        GetMyWatchlistsInput(include_quotes=True), ALICE
    )
    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.quotes_truncated is True
    assert sum(len(r.symbols) for r in quotes.requests) == MAX_QUOTED_SYMBOLS
    assert all(len(r.symbols) <= 20 for r in quotes.requests)
    priced = [e for e in out.groups[0].items if e.close is not None]
    assert len(priced) == MAX_QUOTED_SYMBOLS


async def test_include_quotes_without_a_quote_tool_still_lists_the_symbols() -> None:
    snapshot = WatchlistsSnapshot(groups=(_group("g", ["2330"]),), total_items=1)
    out = await _tools(FakeReader(watchlists=snapshot))["get_my_watchlists"].run(
        GetMyWatchlistsInput(include_quotes=True), ALICE
    )
    assert isinstance(out, GetMyWatchlistsOutput)
    assert out.groups[0].items[0].close is None


# ---- get_my_automations ----------------------------------------------------------------------


async def test_automations_happy_path_and_empty() -> None:
    alert = AlertRuleSummary(
        target="2330 TW",
        condition="收盤價高於 1000",
        status="active",
        trigger_mode="once_per_day",
        last_triggered=NOW,
    )
    strategy = StrategyWatchSummary(
        enabled=True,
        strategies=("波動率擠壓",),
        scope="every stock on today's list",
        morning_list=True,
    )
    full = await _tools(
        FakeReader(
            automations=AutomationsSnapshot(alerts=(alert,), alert_count=1, strategy_watch=strategy)
        )
    )["get_my_automations"].run(GetMyAutomationsInput(), ALICE)
    assert isinstance(full, GetMyAutomationsOutput)
    assert full.alerts[0].condition == "收盤價高於 1000"
    assert full.strategy_watch is not None
    assert full.strategy_watch.enabled

    empty = await _tools(FakeReader())["get_my_automations"].run(GetMyAutomationsInput(), ALICE)
    assert isinstance(empty, GetMyAutomationsOutput)
    assert empty.alerts == ()
    assert empty.schedules == ()
    assert empty.strategy_watch is None
    assert empty.alert_count == 0


# ---- get_my_usage ----------------------------------------------------------------------------


async def test_usage_reports_quota_for_the_context_tier_and_optional_llm_summary() -> None:
    reader = FakeReader(
        usage=UsageSnapshot(
            chat=ChatQuota(used_today=2, limit_per_day=3, remaining_today=1),
            llm_today=LlmUsageToday(calls=4, input_tokens=100, output_tokens=50),
        )
    )
    out = await _tools(reader)["get_my_usage"].run(GetMyUsageInput(include_llm_today=True), ALICE)
    assert isinstance(out, GetMyUsageOutput)
    assert out.tier is Tier.FREE
    assert out.chat.remaining_today == 1
    assert out.llm_today is not None
    assert out.llm_today.calls == 4
    assert reader.calls == [{"tier": Tier.FREE, "include_llm": True}]


# ---- get_my_notifications --------------------------------------------------------------------


def _notification(i: int) -> NotificationSummary:
    return NotificationSummary(
        title=f"n{i}", body="b", source="alert", delivered_at=NOW, read=False
    )


async def test_notifications_default_and_max_limits() -> None:
    reader = FakeReader(notifications=[_notification(i) for i in range(80)])
    tool = _tools(reader)["get_my_notifications"]

    default = await tool.run(GetMyNotificationsInput(), ALICE)
    assert isinstance(default, GetMyNotificationsOutput)
    assert reader.calls[-1]["limit"] == DEFAULT_NOTIFICATIONS == 20
    assert default.count == 20  # the output is cut to the limit even if a reader over-returns

    await tool.run(GetMyNotificationsInput(limit=MAX_NOTIFICATIONS), ALICE)
    assert reader.calls[-1]["limit"] == 50
    with pytest.raises(ValidationError):
        GetMyNotificationsInput(limit=MAX_NOTIFICATIONS + 1)
    with pytest.raises(ValidationError):
        GetMyNotificationsInput(limit=0)


async def test_notifications_empty() -> None:
    out = await _tools(FakeReader())["get_my_notifications"].run(
        GetMyNotificationsInput(unread_only=True), ALICE
    )
    assert isinstance(out, GetMyNotificationsOutput)
    assert out.notifications == ()
    assert out.count == 0
