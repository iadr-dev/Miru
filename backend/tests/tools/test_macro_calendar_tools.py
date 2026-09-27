"""`get_macro_calendar` tool tests — mocked gateway, no network."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from schemas.enums import DataClass, TimingClass, TLSPolicy
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tools.base import ToolContext
from tools.macro_calendar_tools import GetMacroCalendarInput, GetMacroCalendarTool


class _FakeGateway:
    def __init__(self, events: list[dict[str, object]] | None = None) -> None:
        self.calls: list[tuple[str | None, int]] = []
        self._events = events if events is not None else []

    async def fetch_calendar(
        self,
        *,
        impact: str | None = None,
        days_ahead: int = 14,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
    ) -> SourceResult:
        self.calls.append((impact, days_ahead))
        return SourceResult(
            source_id="FINANCE_CALENDAR",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat="caveat",
            payload={"events": self._events},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )


def _event_row(name: str, impact: str = "high") -> dict[str, object]:
    return {
        "market": "GLOBAL",
        "date": date(2026, 9, 30).isoformat(),
        "time_utc": None,
        "name": name,
        "title": f"{name} September 2026",
        "impact": impact,
        "category": "economic-indicators",
        "consensus": None,
        "prior": None,
        "actual": None,
        "url": None,
    }


async def test_run_delegates_and_parses_events() -> None:
    gateway = _FakeGateway(events=[_event_row("US GDP")])
    tool = GetMacroCalendarTool(gateway)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(GetMacroCalendarInput(impact="high", days_ahead=30), ToolContext())

    assert gateway.calls == [("high", 30)]
    assert output.currentness_caveat == "caveat"
    assert output.events[0].name == "US GDP"
    assert output.events[0].market.value == "GLOBAL"


async def test_run_defaults() -> None:
    gateway = _FakeGateway(events=[])
    tool = GetMacroCalendarTool(gateway)

    output = await tool.run(GetMacroCalendarInput(), ToolContext())

    assert gateway.calls == [(None, 14)]
    assert output.events == ()


def test_days_ahead_is_bounded() -> None:
    with pytest.raises(ValueError, match="less than or equal to 90"):
        GetMacroCalendarInput(days_ahead=91)
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        GetMacroCalendarInput(days_ahead=0)


def test_impact_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        GetMacroCalendarInput(impact="extreme")  # type: ignore[arg-type]


async def test_run_handles_no_events_honestly() -> None:
    gateway = _FakeGateway(events=[])
    tool = GetMacroCalendarTool(gateway)

    output = await tool.run(GetMacroCalendarInput(), ToolContext())

    assert output.events == ()
    assert output.currentness_caveat == "caveat"
