"""`TurnBudget` ceilings + the `QuotaReserver`/`QuotaReservation` seam."""

from __future__ import annotations

import pytest

from config.settings import Settings
from orchestrator.ceilings import (
    NoQuotaReserver,
    QuotaReservation,
    TurnBudget,
    TurnCeilingExceededError,
)


def _budget(**over: int) -> TurnBudget:
    base = {
        "max_subagents": 4,
        "max_steps_per_agent": 8,
        "max_tool_calls": 10,
        "max_cost_micros": 1000,
        "max_tokens": 1000,
    }
    base.update(over)
    return TurnBudget(**base)  # type: ignore[arg-type]


def test_from_settings_reads_config_knobs() -> None:
    settings = Settings(
        orch_max_subagents=3,
        orch_max_steps_per_agent=6,
        orch_max_tool_calls_per_turn=25,
    )
    budget = TurnBudget.from_settings(settings)
    assert budget.max_subagents == 3
    assert budget.max_steps_per_agent == 6
    assert budget.max_tool_calls == 25


def test_clamp_subagents_enforces_hard_ceiling() -> None:
    budget = _budget(max_subagents=4)
    assert budget.clamp_subagents(7) == 4
    assert budget.clamp_subagents(2) == 2


def test_tool_call_ceiling_trips() -> None:
    budget = _budget(max_tool_calls=3)
    budget.record_tool_calls(2)
    with pytest.raises(TurnCeilingExceededError) as exc:
        budget.record_tool_calls(2)
    assert exc.value.dimension == "tool_calls"


def test_cost_ceiling_trips() -> None:
    budget = _budget(max_cost_micros=500)
    with pytest.raises(TurnCeilingExceededError) as exc:
        budget.record_cost_micros(600)
    assert exc.value.dimension == "cost_micros"


def test_token_ceiling_trips() -> None:
    budget = _budget(max_tokens=100)
    with pytest.raises(TurnCeilingExceededError):
        budget.record_tokens(101)


async def test_no_quota_reserver_grants_everything() -> None:
    reserver = NoQuotaReserver()
    reservation = await reserver.reserve(skills=("a", "b"), tier="free")
    assert reservation.granted == ("a", "b")
    assert reservation.degraded == ()


def test_quota_reservation_is_degraded() -> None:
    reservation = QuotaReservation(granted=("a",), degraded=("b",))
    assert reservation.is_degraded("b")
    assert not reservation.is_degraded("a")
