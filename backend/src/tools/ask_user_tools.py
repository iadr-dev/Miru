"""`ask_user` — the informational mid-turn clarification tool ("which 台積電, the stock or its
ADR?"), per `docs/backend/modules/07-conversation-streaming.md`'s own note: "Answered by a
normal follow-up message or an option tap. Not an access gate."

Architecture note (why this is a normal, always-runnable `Tool` and not a pause/resume
mechanism): `agent/loop.py::AgentLoop.run_turn`'s Step loop has no suspension point mid-dispatch
— every tool's `run()` must return within the same Step (`_dispatch_one` always resolves
straight-line). So `ask_user` cannot halt the turn and wait for a real answer; instead it:
1. Runs immediately, just validating/echoing back the question+options as its typed output
   (no I/O, no side effect) — exactly like any other tool's `run()`.
2. Opts into `card_type = "ask_user"`, so `agent.loop._card_for`/`_build_card` renders it as a
   governed `RichCard` through the ALREADY-BUILT `rich_card` SSE event + persistence path
   (`orchestrator.Orchestrator._emit_cards`, `api.turn_driver._persist_answer`'s `RichCardBlock`
   loop) — no new event kind, no new persistence block, no hook changes needed.
3. The turn then completes normally (the model is expected to make this its last action for the
   turn, per this tool's own `description`); the user's next message (a normal new turn, or an
   option tap that sends its text as the next message) is how the clarification is "answered" —
   there is no live pause/resume inside one turn, matching the doc's own description.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import DataClass
from tools.base import BaseTool, Tool, ToolContext, parse_args


class AskUserInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    options: tuple[str, ...] = Field(default_factory=tuple)


class AskUserOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str
    options: tuple[str, ...] = Field(default_factory=tuple)


class AskUserTool(BaseTool):
    """`ask_user` — ask the user an informational clarifying question before continuing, with
    optional tappable options. Use ONLY when the user's request is genuinely ambiguous in a way
    that would make an assumption risky (e.g. a symbol/name that resolves to more than one real
    instrument, an unstated date range where the answer materially differs by window). Never use
    this to ask for permission — every tool here always runs, there is no approval gate. This
    must be your ONLY action this turn once called — do not call another tool or write a
    competing final answer in the same turn; the user's next message is how they answer."""

    name = "ask_user"
    description = (
        "Ask the user an informational clarifying question (optionally with a few tappable "
        "options) when their request is genuinely ambiguous. Not a permission/approval "
        "gate — every other tool still always runs regardless. Make this your only action this "
        "turn; the user's next message answers it."
    )
    input_model: type[BaseModel] = AskUserInput
    output_model: type[BaseModel] = AskUserOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "ask_user"

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del ctx  # no I/O — this tool only echoes its own arguments back as typed output
        typed = parse_args(AskUserInput, args)
        return AskUserOutput(question=typed.question, options=typed.options)


def build_registration_table() -> list[tuple[Tool, bool]]:
    return [(AskUserTool(), False)]


__all__ = ["AskUserInput", "AskUserOutput", "AskUserTool", "build_registration_table"]
