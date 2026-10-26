"""Read-only tools over the signed-in user's OWN account data: watchlists, alerts / schedules /
策略盯盤 subscription, daily usage, and the notification inbox.

These are the first `DataClass.PRIVATE` tools. Three rules hold for every one of them:

1. **Signed-in only.** `dispatch_tool` answers an anonymous caller (`ctx.user_id is None`) with a
   calm `SignInRequired` result instead of running the tool; `require_user_id` is the backstop.
2. **The user comes only from the `ToolContext`.** No input model has a user field (and every one
   is `extra="forbid"`), so a prompt-injected argument such as `user_id` is rejected as a bad tool
   argument and can never point a tool at another account.
3. **Read-only and bounded.** No tool changes anything (the `propose_*` tools are the only write
   path, and they only draft a card); every list has a hard cap; nothing here carries a secret
   (no API keys, tokens, channel addresses or device tokens).

`tools/` may not import the stores that hold this data (they live in higher layers), so the reads
sit behind the `AccountDataReader` Protocol. The implementation, `accounts.account_reader`, wraps
the same stores the REST routes use (`WatchlistStore`, `SchedulerStore`, the usage store) - there
is no second copy of the queries - and the composition roots inject it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import DataClass, Tier, UserMarket
from tools.base import BaseTool, Tool, ToolContext, dispatch_tool, parse_args, require_user_id
from tools.market.symbols import GetSymbolQuotesInput, GetSymbolQuotesOutput

if TYPE_CHECKING:
    from tools.registry import ToolRegistry

MAX_WATCHLIST_ITEMS: Final = 200
MAX_QUOTED_SYMBOLS: Final = 60
MAX_ALERTS: Final = 100
MAX_SCHEDULES: Final = 50
DEFAULT_NOTIFICATIONS: Final = 20
MAX_NOTIFICATIONS: Final = 50
MAX_NOTIFICATION_BODY: Final = 300
MAX_USAGE_MODELS: Final = 5
_QUOTE_BATCH: Final = 20

_FRESHNESS: Final = (
    "Read from the user's own account at the time of the call. Close and change figures, when "
    "requested, are the latest end-of-day values on file, not live prices."
)


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------------------------
# Read-side data shapes (what the reader returns; also the pieces of the tool outputs)
# ---------------------------------------------------------------------------------------------


class WatchlistEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: UserMarket
    # Filled only when include_quotes was asked for and the figures exist.
    name: str | None = None
    trade_date: date | None = None
    close: float | None = None
    change_pct: float | None = None


class WatchlistGroupSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    market: UserMarket
    asset_class: Literal["stock", "etf"]
    is_default: bool
    # The group's real size; `items` is cut when the overall cap was reached.
    item_count: int
    items: tuple[WatchlistEntry, ...] = Field(default_factory=tuple)


class WatchlistsSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    groups: tuple[WatchlistGroupSummary, ...] = Field(default_factory=tuple)
    total_items: int = 0
    truncated: bool = False


AlertStatus = Literal["active", "paused", "auto_paused", "expired"]


class AlertRuleSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    target: str = Field(description="The stock (e.g. '2330 TW') or the watchlist group it watches.")
    condition: str = Field(description="The condition in plain fact phrasing.")
    status: AlertStatus
    status_reason: str | None = None
    trigger_mode: str
    last_triggered: datetime | None = None
    expires_at: datetime | None = None


ScheduleStatus = Literal["active", "paused", "error", "skipped_quota"]


class ScheduleSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    briefing: str = Field(description="Which briefing the job produces (its skill reference).")
    cadence: str = Field(description="When it runs, e.g. 'cron 0 8 * * 1-5 (TW)'.")
    status: ScheduleStatus
    status_reason: str | None = None
    last_run: datetime | None = None
    next_run: datetime | None = None


class StrategyWatchSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool
    strategies: tuple[str, ...]
    scope: str = Field(description="'every stock on today's list' or the watchlist group's name.")
    morning_list: bool
    daily_limit: int | None = None


class AutomationsSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: tuple[AlertRuleSummary, ...] = Field(default_factory=tuple)
    alert_count: int = 0
    schedules: tuple[ScheduleSummary, ...] = Field(default_factory=tuple)
    schedule_count: int = 0
    strategy_watch: StrategyWatchSummary | None = None


class ChatQuota(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    used_today: int
    limit_per_day: int | None = Field(description="None = no daily chat limit applies.")
    remaining_today: int | None = None
    unlimited_because: Literal["tier", "own_key"] | None = None
    window: str = "UTC calendar day"


class ModelUsageToday(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    calls: int
    tokens: int


class LlmUsageToday(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    top_models: tuple[ModelUsageToday, ...] = Field(default_factory=tuple)


class UsageSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    chat: ChatQuota
    llm_today: LlmUsageToday | None = None


class NotificationSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    body: str
    source: str
    delivered_at: datetime
    read: bool


class AccountDataReader(Protocol):
    """The user-scoped reads behind the account tools. Every method takes the user id explicitly
    and returns only that user's rows; the implementation lives above `tools/`."""

    async def watchlists(
        self, *, user_id: str, market: UserMarket | None, max_items: int
    ) -> WatchlistsSnapshot: ...

    async def automations(
        self, *, user_id: str, max_alerts: int, max_schedules: int
    ) -> AutomationsSnapshot: ...

    async def usage(self, *, user_id: str, tier: Tier, include_llm: bool) -> UsageSnapshot: ...

    async def notifications(
        self, *, user_id: str, limit: int, unread_only: bool
    ) -> list[NotificationSummary]: ...


# ---------------------------------------------------------------------------------------------
# get_my_watchlists
# ---------------------------------------------------------------------------------------------


class GetMyWatchlistsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: UserMarket | None = Field(
        default=None, description="Only this market's groups (TW or US); omit for all."
    )
    include_quotes: bool = Field(
        default=False,
        description=(
            "Add each stock's name, latest close and daily change % (end of day). Bounded to the "
            f"first {MAX_QUOTED_SYMBOLS} symbols."
        ),
    )


class GetMyWatchlistsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signed_in: Literal[True] = True
    groups: tuple[WatchlistGroupSummary, ...] = Field(default_factory=tuple)
    total_items: int = 0
    # True when more than MAX_WATCHLIST_ITEMS symbols exist and the lists are cut.
    truncated: bool = False
    quotes_included: bool = False
    # True when include_quotes covered only the first MAX_QUOTED_SYMBOLS symbols.
    quotes_truncated: bool = False
    # Set when there are no groups: an empty list is a normal state, not an error or a failed read.
    note: str | None = None
    as_of: datetime
    currentness_caveat: str = _FRESHNESS


NO_WATCHLISTS_NOTE: Final = (
    "No watchlists yet. This is normal, not an error: the app creates the starter watchlists the "
    "first time the user opens their watchlist page, and this read never creates anything. The "
    "user can open the watchlist page to get the starter set or create their own."
)


class GetMyWatchlistsTool(BaseTool):
    name = "get_my_watchlists"
    description = (
        "List the signed-in user's own watchlists (自選股): each group with its market, size and "
        f"symbols (up to {MAX_WATCHLIST_ITEMS} in total), optionally with each stock's latest "
        "close and daily change. Read-only; only ever the caller's own data."
    )
    input_model: type[BaseModel] = GetMyWatchlistsInput
    output_model: type[BaseModel] = GetMyWatchlistsOutput
    data_class = DataClass.PRIVATE
    card_type: str | None = None

    def __init__(self, reader: AccountDataReader, registry: ToolRegistry) -> None:
        self._reader = reader
        self._registry = registry

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetMyWatchlistsInput, args)
        snapshot = await self._reader.watchlists(
            user_id=require_user_id(ctx), market=typed.market, max_items=MAX_WATCHLIST_ITEMS
        )
        groups = snapshot.groups
        quotes_truncated = False
        if typed.include_quotes:
            groups, quotes_truncated = await self._with_quotes(groups, ctx)
        return GetMyWatchlistsOutput(
            groups=groups,
            total_items=snapshot.total_items,
            truncated=snapshot.truncated,
            quotes_included=typed.include_quotes,
            quotes_truncated=quotes_truncated,
            note=None if groups else NO_WATCHLISTS_NOTE,
            as_of=_now(),
        )

    async def _with_quotes(
        self, groups: tuple[WatchlistGroupSummary, ...], ctx: ToolContext
    ) -> tuple[tuple[WatchlistGroupSummary, ...], bool]:
        """Join the latest close and change through the existing `get_symbol_quotes` tool, a
        batch per market, for the first MAX_QUOTED_SYMBOLS distinct symbols."""
        tool = self._registry.get_tool("get_symbol_quotes")
        wanted, truncated = _quote_requests(groups)
        if tool is None:
            return groups, truncated
        found: dict[tuple[str, str], WatchlistEntry] = {}
        for market, symbols in wanted.items():
            for start in range(0, len(symbols), _QUOTE_BATCH):
                batch = tuple(symbols[start : start + _QUOTE_BATCH])
                result = await dispatch_tool(
                    tool,
                    GetSymbolQuotesInput(symbols=batch, market=market, days=2),
                    ctx,
                )
                if not isinstance(result, GetSymbolQuotesOutput):
                    continue  # a refusal or teaser: leave those rows without figures
                found.update(_quoted_entries(market, result))
        merged = tuple(
            group.model_copy(
                update={
                    "items": tuple(found.get((e.market.value, e.symbol), e) for e in group.items)
                }
            )
            for group in groups
        )
        return merged, truncated


def _quote_requests(
    groups: tuple[WatchlistGroupSummary, ...],
) -> tuple[dict[UserMarket, list[str]], bool]:
    """The distinct symbols to quote per market (first MAX_QUOTED_SYMBOLS in encounter order)
    and whether any further distinct symbol was left out."""
    wanted: dict[UserMarket, list[str]] = {}
    seen = 0
    truncated = False
    for entry in (e for group in groups for e in group.items):
        bucket = wanted.setdefault(entry.market, [])
        if entry.symbol in bucket:
            continue
        if seen >= MAX_QUOTED_SYMBOLS:
            truncated = True
            continue
        bucket.append(entry.symbol)
        seen += 1
    return wanted, truncated


def _quoted_entries(
    market: UserMarket, result: GetSymbolQuotesOutput
) -> dict[tuple[str, str], WatchlistEntry]:
    return {
        (market.value, row.symbol): WatchlistEntry(
            symbol=row.symbol,
            market=market,
            name=row.name,
            trade_date=row.trade_date,
            close=row.close,
            change_pct=row.change_pct,
        )
        for row in result.rows
    }


# ---------------------------------------------------------------------------------------------
# get_my_automations
# ---------------------------------------------------------------------------------------------


class GetMyAutomationsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GetMyAutomationsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signed_in: Literal[True] = True
    alerts: tuple[AlertRuleSummary, ...] = Field(default_factory=tuple)
    alert_count: int = 0
    schedules: tuple[ScheduleSummary, ...] = Field(default_factory=tuple)
    schedule_count: int = 0
    # None = the user has no 策略盯盤 subscription.
    strategy_watch: StrategyWatchSummary | None = None
    as_of: datetime
    currentness_caveat: str = (
        "Read from the user's own account at the time of the call. An alert's condition is a fact "
        "about the data (e.g. a close above a level), not a recommendation."
    )


class GetMyAutomationsTool(BaseTool):
    name = "get_my_automations"
    description = (
        "List the signed-in user's own alert rules (condition, status, last triggered), scheduled "
        "briefings (cadence, next run) and 策略盯盤 subscription. Read-only; only the caller's "
        "own data. To change any of these the user confirms a proposal card."
    )
    input_model: type[BaseModel] = GetMyAutomationsInput
    output_model: type[BaseModel] = GetMyAutomationsOutput
    data_class = DataClass.PRIVATE
    card_type: str | None = None

    def __init__(self, reader: AccountDataReader) -> None:
        self._reader = reader

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        parse_args(GetMyAutomationsInput, args)
        snapshot = await self._reader.automations(
            user_id=require_user_id(ctx), max_alerts=MAX_ALERTS, max_schedules=MAX_SCHEDULES
        )
        return GetMyAutomationsOutput(
            alerts=snapshot.alerts[:MAX_ALERTS],
            alert_count=snapshot.alert_count,
            schedules=snapshot.schedules[:MAX_SCHEDULES],
            schedule_count=snapshot.schedule_count,
            strategy_watch=snapshot.strategy_watch,
            as_of=_now(),
        )


# ---------------------------------------------------------------------------------------------
# get_my_usage
# ---------------------------------------------------------------------------------------------


class GetMyUsageInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    include_llm_today: bool = Field(
        default=False, description="Also summarise today's model calls and tokens."
    )


class GetMyUsageOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signed_in: Literal[True] = True
    tier: Tier
    chat: ChatQuota
    llm_today: LlmUsageToday | None = None
    as_of: datetime
    currentness_caveat: str = (
        "Counts for the current UTC day at the time of the call; the allowance resets at 00:00 UTC."
    )


class GetMyUsageTool(BaseTool):
    name = "get_my_usage"
    description = (
        "The signed-in user's AI chat usage today against their daily allowance (by plan), and "
        "optionally today's model calls and token totals. Read-only; only the caller's own data."
    )
    input_model: type[BaseModel] = GetMyUsageInput
    output_model: type[BaseModel] = GetMyUsageOutput
    data_class = DataClass.PRIVATE
    card_type: str | None = None

    def __init__(self, reader: AccountDataReader) -> None:
        self._reader = reader

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetMyUsageInput, args)
        snapshot = await self._reader.usage(
            user_id=require_user_id(ctx), tier=ctx.tier, include_llm=typed.include_llm_today
        )
        return GetMyUsageOutput(
            tier=ctx.tier, chat=snapshot.chat, llm_today=snapshot.llm_today, as_of=_now()
        )


# ---------------------------------------------------------------------------------------------
# get_my_notifications
# ---------------------------------------------------------------------------------------------


class GetMyNotificationsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    limit: int = Field(default=DEFAULT_NOTIFICATIONS, ge=1, le=MAX_NOTIFICATIONS)
    unread_only: bool = False


class GetMyNotificationsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signed_in: Literal[True] = True
    notifications: tuple[NotificationSummary, ...] = Field(default_factory=tuple)
    count: int = 0
    as_of: datetime
    currentness_caveat: str = (
        "The user's most recent in-app notifications, newest first. Reading them here does not "
        "mark them read."
    )


class GetMyNotificationsTool(BaseTool):
    name = "get_my_notifications"
    description = (
        f"The signed-in user's most recent in-app notifications (default {DEFAULT_NOTIFICATIONS}, "
        f"at most {MAX_NOTIFICATIONS}), newest first. Read-only: does not mark anything read. "
        "Only the caller's own data."
    )
    input_model: type[BaseModel] = GetMyNotificationsInput
    output_model: type[BaseModel] = GetMyNotificationsOutput
    data_class = DataClass.PRIVATE
    card_type: str | None = None

    def __init__(self, reader: AccountDataReader) -> None:
        self._reader = reader

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetMyNotificationsInput, args)
        rows = await self._reader.notifications(
            user_id=require_user_id(ctx), limit=typed.limit, unread_only=typed.unread_only
        )
        rows = rows[: typed.limit]
        return GetMyNotificationsOutput(notifications=tuple(rows), count=len(rows), as_of=_now())


def build_account_tools(reader: AccountDataReader, registry: ToolRegistry) -> list[Tool]:
    """The four account tools. `registry` is only used by `get_my_watchlists` to reach the existing
    `get_symbol_quotes` tool, so the tools are registered after the market tools exist."""
    return [
        GetMyWatchlistsTool(reader, registry),
        GetMyAutomationsTool(reader),
        GetMyUsageTool(reader),
        GetMyNotificationsTool(reader),
    ]


__all__ = [
    "DEFAULT_NOTIFICATIONS",
    "MAX_ALERTS",
    "MAX_NOTIFICATIONS",
    "MAX_NOTIFICATION_BODY",
    "MAX_QUOTED_SYMBOLS",
    "MAX_SCHEDULES",
    "MAX_USAGE_MODELS",
    "MAX_WATCHLIST_ITEMS",
    "NO_WATCHLISTS_NOTE",
    "AccountDataReader",
    "AlertRuleSummary",
    "AutomationsSnapshot",
    "ChatQuota",
    "GetMyAutomationsInput",
    "GetMyAutomationsOutput",
    "GetMyAutomationsTool",
    "GetMyNotificationsInput",
    "GetMyNotificationsOutput",
    "GetMyNotificationsTool",
    "GetMyUsageInput",
    "GetMyUsageOutput",
    "GetMyUsageTool",
    "GetMyWatchlistsInput",
    "GetMyWatchlistsOutput",
    "GetMyWatchlistsTool",
    "LlmUsageToday",
    "ModelUsageToday",
    "NotificationSummary",
    "ScheduleSummary",
    "StrategyWatchSummary",
    "UsageSnapshot",
    "WatchlistEntry",
    "WatchlistGroupSummary",
    "WatchlistsSnapshot",
    "build_account_tools",
]
