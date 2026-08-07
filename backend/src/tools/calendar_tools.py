"""`get_calendar_events` — the tool-facing wrapper over `calendar/events.py`'s Layer-2
composition (除權息/ETF配息 + CB 賣回權/到期 today; see that module's docstring for exactly which
feeds are composed and which, e.g. AGM/法說會, are an honest, disclosed gap rather than a
fabricated mapping).

This tool takes no Layer-1 rule-engine markers of its own — generating those (財報/月營收
deadlines, derivatives settlement, AGM-derived dates) requires caller-supplied context this
tool's bare `(symbol, date range)` input does not carry (a fiscal year/quarter, an already-known
AGM date, …); wiring `calendar/rules.py` output into this same view is a `calendar/rules.py`-
consuming caller's job, not this tool's. What this tool returns today is exactly the feed-
sourced half of `calendar/events.py::get_calendar_events` — real 除權息/CB events, not rule
markers.

`Capability.CB_PUT_PROVISION` is Sponsor-tier at the source, so this tool is classed `sponsor`
as a whole (the existing bundling convention for a mixed public+sponsor composite, e.g.
`get_derivatives_institutional`) rather than attempting a field-level entitlement split.
"""

from __future__ import annotations

from calendar.events import get_calendar_events
from datetime import date as date_

from pydantic import BaseModel, ConfigDict, Field

from dataaccess.router import DataAccessRouter
from schemas.enums import DataClass
from schemas.source import HealthStatus
from tools.base import BaseTool, Tool, ToolContext, parse_args
from tools.common import DateRangeInput


class CalendarEventOut(BaseModel):
    """Tool-output mirror of `calendar.models.CalendarEvent` — a distinct type from that
    module's own model so `tools/` never re-exports a `calendar/`-owned Pydantic model
    directly as a tool's `output_model` (keeps the tool-output schema decoupled from that
    module's internal shape)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    stock_id: str | None = None
    type: str
    title_zh: str
    title_en: str
    event_date: date_ | None = None
    window_start: date_ | None = None
    window_end: date_ | None = None
    state: str
    source: str


class GetCalendarEventsOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    currentness_caveat: str = Field(min_length=1)
    health_status: HealthStatus
    events: tuple[CalendarEventOut, ...] = Field(default_factory=tuple)


class GetCalendarEventsTool(BaseTool):
    """`get_calendar_events` — feed-sourced forward events (除權息/ETF配息, CB 賣回權/到期) for one
    stock/ETF/CB symbol over a bounded date range. See this module's own docstring for what is
    (and, honestly, is not) composed."""

    name = "get_calendar_events"
    description = (
        "Get feed-sourced upcoming calendar events (dividend/ex-rights dates, convertible-bond "
        "put/maturity dates) for one stock/ETF/CB symbol over a bounded date range."
    )
    input_model: type[BaseModel] = DateRangeInput
    output_model: type[BaseModel] = GetCalendarEventsOutput
    data_class = DataClass.SPONSOR
    # Stays card_type=None (the default) — an event/notice stream, per this registry's own
    # convention that calendar/announcement feeds render better as prose/list than as a card.

    def __init__(self, router: DataAccessRouter) -> None:
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(DateRangeInput, args)
        symbol = typed.to_symbol()
        start_date = typed.start_date or date_.today()
        end_date = typed.end_date or start_date
        events, result = await get_calendar_events(
            self._router,
            symbol,
            start_date=start_date,
            end_date=end_date,
            tls_policy=ctx.tls_policy,
        )
        return GetCalendarEventsOutput(
            source_id=result.source_id,
            currentness_caveat=result.currentness_caveat,
            health_status=result.health.status,
            events=tuple(
                CalendarEventOut(
                    id=event.id,
                    stock_id=event.stock_id,
                    type=event.type.value,
                    title_zh=event.title_zh,
                    title_en=event.title_en,
                    event_date=event.event_date,
                    window_start=event.window_start,
                    window_end=event.window_end,
                    state=event.state.value,
                    source=event.source,
                )
                for event in events
            ),
        )


def build_registration_table(router: DataAccessRouter) -> list[tuple[Tool, bool]]:
    return [(GetCalendarEventsTool(router), False)]


__all__ = [
    "CalendarEventOut",
    "GetCalendarEventsOutput",
    "GetCalendarEventsTool",
    "build_registration_table",
]
