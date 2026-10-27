"""`get_macro_calendar` — real US/global macro-release events (financecalendar.com's
`wp-json/fc/v1/calendar`, see `sources/financecalendar.py`'s own docstring for why this is a
distinct, additive feed from `get_calendar_events_feed`'s existing yfinance-backed economic
calendar). Not `(capability, symbol)`-scoped — a calendar query has no symbol — reached via
`dataaccess/financecalendar.py`'s gateway, the same non-router carve-out `news_tools.py` already
establishes for `get_news_feed`.
"""

from __future__ import annotations

from datetime import date as date_
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dataaccess.financecalendar import FinanceCalendarGateway
from schemas.capabilities import MacroReleaseEventRow
from schemas.enums import DataClass
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args

_MAX_DAYS_AHEAD = 90
_MAX_RANGE_DAYS = 92


class GetMacroCalendarInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    impact: Literal["high", "medium", "low"] | None = Field(
        default=None, description="Filter to one impact level; omitted returns every level."
    )
    days_ahead: int = Field(
        default=14,
        ge=1,
        le=_MAX_DAYS_AHEAD,
        description="Bounded look-ahead window in days from today. Ignored if from_date/to_date "
        "are given.",
    )
    from_date: date_ | None = Field(
        default=None,
        description="Explicit range start (inclusive) — e.g. paging back to a prior month. Must "
        "be given together with to_date; overrides days_ahead.",
    )
    to_date: date_ | None = Field(
        default=None,
        description="Explicit range end (inclusive). Must be given together with from_date.",
    )

    @model_validator(mode="after")
    def _validate_range(self) -> GetMacroCalendarInput:
        if (self.from_date is None) != (self.to_date is None):
            raise ValueError("from_date and to_date must be given together")
        if self.from_date is not None and self.to_date is not None:
            if self.to_date < self.from_date:
                raise ValueError("to_date must not be before from_date")
            if (self.to_date - self.from_date).days > _MAX_RANGE_DAYS:
                raise ValueError(f"from_date/to_date range must not exceed {_MAX_RANGE_DAYS} days")
        return self


class GetMacroCalendarOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    # An empty result is ambiguous on its own: `unreachable` means the source could not be fetched
    # (an outage, NOT "nothing there"), `stale` means it answered with nothing.
    health_status: HealthStatus
    events: tuple[MacroReleaseEventRow, ...] = Field(default_factory=tuple)


class GetMacroCalendarTool(BaseTool):
    """`get_macro_calendar` — US/global macro-release events (GDP/CPI/PCE/PMI/NFP/FOMC and
    international central-bank/CPI releases) with consensus/prior/actual, from
    financecalendar.com. Defaults to an upcoming look-ahead window; an explicit `from_date`/
    `to_date` pair (confirmed live to work for genuinely past ranges too) lets a caller page back
    to a prior month/week instead of only ever seeing "upcoming". Public — the upstream itself
    carries no entitlement tier; distinct from `get_macro_indicators`, which stays Sponsor-gated
    for its own existing reasons."""

    name = "get_macro_calendar"
    description = (
        "Get US/global macro-economic release events (GDP, CPI, PCE, PMI, jobs report, "
        "central-bank rate decisions) with consensus/prior/actual figures — either an upcoming "
        "look-ahead window (days_ahead) or an explicit from_date/to_date range (including past "
        "dates), with an optional impact-level filter."
    )
    input_model: type[BaseModel] = GetMacroCalendarInput
    output_model: type[BaseModel] = GetMacroCalendarOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "macro_calendar"

    def __init__(self, gateway: FinanceCalendarGateway | None = None) -> None:
        self._gateway = gateway or FinanceCalendarGateway()

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(GetMacroCalendarInput, args)
        result = await self._gateway.fetch_calendar(
            impact=typed.impact,
            days_ahead=typed.days_ahead,
            from_date=typed.from_date,
            to_date=typed.to_date,
            tls_policy=ctx.tls_policy,
        )
        raw_events = result.payload.get("events")
        events: tuple[MacroReleaseEventRow, ...] = ()
        if isinstance(raw_events, list):
            typed_events = cast("list[dict[str, object]]", raw_events)
            events = tuple(MacroReleaseEventRow.model_validate(event) for event in typed_events)
        return GetMacroCalendarOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            events=events,
        )


def build_registration_table(
    gateway: FinanceCalendarGateway | None = None,
) -> list[tuple[Tool, bool]]:
    return [(GetMacroCalendarTool(gateway), False)]


__all__ = [
    "GetMacroCalendarInput",
    "GetMacroCalendarOutput",
    "GetMacroCalendarTool",
    "build_registration_table",
]
