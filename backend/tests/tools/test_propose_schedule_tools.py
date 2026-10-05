"""`propose_schedule` is propose-only: it validates the template + targets and echoes the exact
trigger/action bodies `POST /schedules` takes. It never creates anything."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.loop import _build_card
from orchestrator.composition import build_skill_registry
from schemas.enums import DataClass, Market
from schemas.schedule_templates import SCHEDULE_TEMPLATES, ScheduleTemplateId
from tools.base import ToolContext
from tools.propose_schedule_tools import (
    ProposeScheduleInput,
    ProposeScheduleOutput,
    ProposeScheduleTool,
)


async def _run(**kwargs: object) -> ProposeScheduleOutput:
    out = await ProposeScheduleTool().run(
        ProposeScheduleInput.model_validate(kwargs), ToolContext()
    )
    assert isinstance(out, ProposeScheduleOutput)
    return out


async def test_a_brief_resolves_to_the_trigger_and_skill_the_api_takes() -> None:
    out = await _run(template="pre_open_brief", targets=["2330", "2317"])

    assert out.trigger == {"type": "market_event", "spec": "pre_open", "market": "TW"}
    assert out.action == {
        "skill_ref": "daily-context-brief",
        "inputs": {"stock_ids": ["2330", "2317"]},
    }
    assert out.requires_user_confirmation is True


async def test_single_target_skills_get_a_scalar_input_and_fixed_extras() -> None:
    chip = await _run(template="after_close_chip", targets=["2330"])
    assert chip.action["inputs"] == {"stock_id": "2330"}

    us = await _run(template="us_stock_report", targets=["aapl"])
    assert us.market is Market.US
    assert us.action == {
        "skill_ref": "stock-report-us",
        "inputs": {"market": "US", "symbol_id": "AAPL"},
    }


def test_single_target_templates_reject_several_targets() -> None:
    with pytest.raises(ValidationError):
        ProposeScheduleInput.model_validate(
            {"template": "after_close_chip", "targets": ["2330", "2317"]}
        )


@pytest.mark.parametrize("bad", [["bad code!"], [""], []])
def test_targets_must_be_ticker_codes(bad: list[str]) -> None:
    with pytest.raises(ValidationError):
        ProposeScheduleInput.model_validate({"template": "pre_open_brief", "targets": bad})


def test_the_model_cannot_pick_a_raw_cron_or_an_arbitrary_skill() -> None:
    with pytest.raises(ValidationError):
        ProposeScheduleInput.model_validate(
            {"template": "pre_open_brief", "targets": ["2330"], "cron": "* * * * *"}
        )
    with pytest.raises(ValidationError):
        ProposeScheduleInput.model_validate({"template": "delete_everything", "targets": ["2330"]})


def test_every_template_names_a_registered_skill() -> None:
    registry = build_skill_registry()
    for template in SCHEDULE_TEMPLATES.values():
        registry.get(template.skill_ref)  # raises if unknown


def test_every_catalogue_id_has_a_template() -> None:
    assert set(SCHEDULE_TEMPLATES) == set(ScheduleTemplateId)


async def test_it_renders_a_governed_card_and_is_public_data() -> None:
    out = await _run(template="tw_etf_report", targets=["0050"])
    card = _build_card("schedule_proposal", out)

    assert card.card_type == "schedule_proposal"
    assert card.data["targets"] == ["0050"]
    assert ProposeScheduleTool.data_class is DataClass.PUBLIC
