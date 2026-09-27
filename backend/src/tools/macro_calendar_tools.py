"""`get_macro_calendar` — real US/global macro-release events (financecalendar.com's
`wp-json/fc/v1/calendar`, see `sources/financecalendar.py`'s own docstring for why this is a
distinct, additive feed from `get_calendar_events_feed`'s existing yfinance-backed economic
calendar). Not `(capability, symbol)`-scoped — a calendar query has no symbol — reached via
`dataaccess/financecalendar.py`'s gateway, the same non-router carve-out `news_tools.py` already
establishes for `get_news_feed`.
"""

from __future__ import annotations

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.financecalendar import FinanceCalendarGateway
from schemas.capabilities import MacroReleaseEventRow
from schemas.enums import DataClass
from tools.base import BaseTool, Tool, ToolContext, parse_args

_MAX_DAYS_AHEAD = 90


class GetMacroCalendarInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    impact: Literal["high", "medium", "low"] | None = Field(
        default=None, description="Filter to one impact level; omitted returns every level."
    )
    days_ahead: int = Field(
        default=14,
        ge=1,
        le=_MAX_DAYS_AHEAD,
        description="Bounded look-ahead window in days from today.",
    )


class GetMacroCalendarOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    currentness_caveat: str = Field(min_length=1)
    events: tuple[MacroReleaseEventRow, ...] = Field(default_factory=tuple)


class GetMacroCalendarTool(BaseTool):
    """`get_macro_calendar` — upcoming US/global macro-release events (GDP/CPI/PCE/PMI/NFP/FOMC
    and international central-bank/CPI releases) with consensus/prior/actual, from
    financecalendar.com. Public — the upstream itself carries no entitlement tier; distinct from
    `get_macro_indicators`, which stays Sponsor-gated for its own existing reasons."""

    name = "get_macro_calendar"
    description = (
        "Get upcoming US/global macro-economic release events (GDP, CPI, PCE, PMI, jobs report, "
        "central-bank rate decisions) with consensus/prior/actual figures, bounded to a look-"
        "ahead window and optional impact-level filter."
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
            impact=typed.impact, days_ahead=typed.days_ahead, tls_policy=ctx.tls_policy
        )
        raw_events = result.payload.get("events")
        events: tuple[MacroReleaseEventRow, ...] = ()
        if isinstance(raw_events, list):
            typed_events = cast("list[dict[str, object]]", raw_events)
            events = tuple(MacroReleaseEventRow.model_validate(event) for event in typed_events)
        return GetMacroCalendarOutput(currentness_caveat=result.currentness_caveat, events=events)


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
