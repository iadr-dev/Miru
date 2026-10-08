"""`propose_memory` - the agent's way to *suggest* remembering something ("記住我主要看高股息").

Propose-only, exactly like `propose_alert`: this tool performs no I/O and saves nothing. It
validates what the model assembled from the user's words and echoes it back as a typed
`memory_proposal` card. The user then taps 儲存 and the *frontend* calls the ordinary
`POST /memory` (auth and the governance scan for credentials / trading language / tone
instructions apply there; the card itself checks the user's pause switch). The confirm gate is the
card, so there is no agent-side write for a confused model or a prompt-injected page to abuse.

A proposal the store would reject (a tone instruction, a credential, buy/sell language) is returned
to the model as a tool-argument error it can correct, instead of reaching the user as a card they
can only fail to save.
"""

from __future__ import annotations

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from schemas.enums import DataClass
from schemas.memory_rules import (
    scan_content_for_governance_violations,
    scan_preference_for_style_directive,
)
from tools.base import BaseTool, Tool, ToolContext, parse_args

_TEXT_MAX: Final = 300


class ProposeMemoryInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Not named `type`: Gemini mis-parses a function argument literally called "type" (a JSON-schema
    # keyword) and sends the call with empty arguments.
    memory_type: Literal["preference", "finding", "entity_note"] = Field(
        description=(
            "preference = the user's analysis focus (markets, horizon, sectors or ETFs they "
            "follow); finding = a fact or note they asked to keep; entity_note = a note about one "
            "stock/ETF. NEVER how Miru should talk - tone and format live in Tone settings."
        )
    )
    text: str = Field(
        min_length=1,
        max_length=_TEXT_MAX,
        description="What to remember, as one short plain sentence in the user's language.",
    )

    @field_validator("text")
    @classmethod
    def _clean(cls, value: str) -> str:
        return " ".join(value.split())

    @model_validator(mode="after")
    def _governed(self) -> Self:
        content: dict[str, object] = {"text": self.text}
        violation = scan_content_for_governance_violations(content)
        if violation is None and self.memory_type == "preference":
            violation = scan_preference_for_style_directive(content)
        if violation is not None:
            raise ValueError(violation)
        return self


class ProposeMemoryOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_type: Literal["preference", "finding", "entity_note"]
    text: str
    # Always true: nothing has been saved. The user must confirm on the card.
    requires_user_confirmation: Literal[True] = True
    next_step: str = (
        "DONE: the confirmation card is now on the user's screen. Do NOT call propose_memory "
        "again this turn. Reply with one short sentence and stop."
    )


class ProposeMemoryTool(BaseTool):
    """`propose_memory` - propose (never save) a long-term memory for the user to confirm."""

    name = "propose_memory"
    description = (
        "Propose remembering something about the user's analysis focus when they ask you to "
        "remember it (e.g. '記住我主要關注高股息 ETF', 'remember I follow semiconductors'). This "
        "ONLY shows a confirmation card - nothing is saved until the user taps 儲存 on it. Call it "
        "once per memory, then say briefly that the proposal is on screen; never say it was saved. "
        "Do NOT use it for tone, style or answer-format requests ('be more casual', 'use "
        "bullets') - tell the user those are changed in Tone settings (語氣設定). Never include "
        "keys, passwords or buy/sell advice."
    )
    input_model: type[BaseModel] = ProposeMemoryInput
    output_model: type[BaseModel] = ProposeMemoryOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "memory_proposal"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no I/O, no identity needed - the signed-in user saves it from the card
        typed = parse_args(ProposeMemoryInput, args)
        return ProposeMemoryOutput(memory_type=typed.memory_type, text=typed.text)


def build_registration_table() -> list[tuple[Tool, bool]]:
    return [(ProposeMemoryTool(), False)]


__all__ = [
    "ProposeMemoryInput",
    "ProposeMemoryOutput",
    "ProposeMemoryTool",
    "build_registration_table",
]
