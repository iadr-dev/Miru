"""`propose_schedule` — the agent's way to *suggest* a recurring briefing in chat ("每天開盤前幫我
看一下 2330 的簡報").

Propose-only, exactly like `propose_alert` (governance §9a bound 1): this tool performs no I/O and
creates nothing. It validates the template + targets the model assembled and echoes back a typed
`schedule_proposal` card carrying the resolved trigger and skill action; the user taps 建立 and the
*frontend* calls the ordinary `POST /schedules`, which enforces auth, the fair-use caps and the
trigger/skill validation. The model picks a catalogue template, never a raw cron or a skill name, so
it cannot schedule anything outside `schemas.schedule_templates`.
"""

from __future__ import annotations

import re
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.enums import DataClass, Market
from schemas.schedule_templates import (
    MAX_TARGETS,
    SCHEDULE_TEMPLATES,
    ScheduleTemplateId,
    build_inputs,
)
from tools.base import BaseTool, Tool, ToolContext, parse_args

_TARGET_PATTERN: Final = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,15}$")


class ProposeScheduleInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template: ScheduleTemplateId = Field(
        description=(
            "Which briefing. pre_open_brief: a short daily pre-open brief for one or more Taiwan "
            "stocks. after_close_chip: a daily after-close chip-flow review of ONE Taiwan stock. "
            "disposition_watch: a daily after-close attention/disposition check on ONE Taiwan "
            "stock. weekly_catalyst: a Monday announcements/insider scan for ONE Taiwan stock. "
            "The four FULL analysis reports (5-year tables, charts, seasonality, risk), each for "
            "ONE symbol and sent weekly: tw_stock_report (Taiwan stock), us_stock_report (US "
            "stock), tw_etf_report (Taiwan ETF), us_etf_report (US ETF)."
        )
    )
    targets: list[str] = Field(
        min_length=1,
        max_length=MAX_TARGETS,
        description=(
            "Ticker codes, e.g. ['2330'] or ['2330', '2317']. Only pre_open_brief takes more than "
            "one; every other template takes exactly one."
        ),
    )

    @field_validator("targets")
    @classmethod
    def _normalise_targets(cls, value: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(item.strip().upper() for item in value if item.strip()))
        if not cleaned:
            raise ValueError("at least one ticker code is required")
        for item in cleaned:
            if not _TARGET_PATTERN.match(item):
                raise ValueError(f"'{item}' is not a ticker code")
        return cleaned

    @model_validator(mode="after")
    def _arity(self) -> Self:
        template = SCHEDULE_TEMPLATES[self.template]
        if not template.multiple and len(self.targets) != 1:
            raise ValueError(f"'{self.template.value}' takes exactly one ticker code")
        return self


class ProposeScheduleOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    template: ScheduleTemplateId
    targets: list[str]
    market: Market
    cadence: str
    # The exact bodies `POST /schedules` takes — the card posts these as-is.
    trigger: dict[str, object]
    action: dict[str, object]
    summary: str
    # Always true: nothing has been created. The user must confirm on the card.
    requires_user_confirmation: Literal[True] = True
    # Read by the model as part of the tool result (see `ProposeAlertOutput.next_step`).
    next_step: str = (
        "DONE: the confirmation card is now on the user's screen. Do NOT call propose_schedule "
        "again this turn. Reply with one short sentence and stop."
    )


class ProposeScheduleTool(BaseTool):
    """`propose_schedule` — propose (never create) a recurring briefing for the user to confirm."""

    name = "propose_schedule"
    description = (
        "Propose a recurring briefing (a scheduled analysis) when the user asks to be briefed "
        "regularly, e.g. '每天開盤前幫我看 2330', 'send me a weekly ETF report'. This ONLY shows "
        "a confirmation card — it creates nothing; the schedule exists only after the user taps "
        "建立. Call it once per schedule, then tell the user briefly that the proposal is on "
        "screen; never say it was created or set. Use ask_user first if the stock or cadence is "
        "unclear. Briefings describe data; they are never trade signals."
    )
    input_model: type[BaseModel] = ProposeScheduleInput
    output_model: type[BaseModel] = ProposeScheduleOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "schedule_proposal"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no I/O, no identity needed — the signed-in user creates the job from the card
        typed = parse_args(ProposeScheduleInput, args)
        template = SCHEDULE_TEMPLATES[typed.template]
        return ProposeScheduleOutput(
            template=typed.template,
            targets=typed.targets,
            market=template.market,
            cadence=template.cadence,
            trigger={
                "type": template.trigger_type,
                "spec": template.trigger_spec,
                "market": template.market.value,
            },
            action={
                "skill_ref": template.skill_ref,
                "inputs": build_inputs(template, typed.targets),
            },
            summary=f"{template.cadence}：{'、'.join(typed.targets)}",
        )


def build_registration_table() -> list[tuple[Tool, bool]]:
    return [(ProposeScheduleTool(), False)]


__all__ = [
    "ProposeScheduleInput",
    "ProposeScheduleOutput",
    "ProposeScheduleTool",
    "build_registration_table",
]
