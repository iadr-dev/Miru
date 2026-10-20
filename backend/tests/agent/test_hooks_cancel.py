"""`agent.hooks.CancelToken` — cooperative cancellation between Steps, and `AgentLoop`'s
`post_tool`/`pre_llm` extension seams composed together."""

from __future__ import annotations

import pytest

from agent.loop import AgentLoop, TurnCancelledError
from tests.agent.conftest import EchoTool
from tests.llm.stubs import StubLLMClient, make_text_response, make_tool_call_response
from tools.base import ToolContext


class _AlwaysCancelledToken:
    def is_cancelled(self) -> bool:
        return True


class _NeverCancelledToken:
    def is_cancelled(self) -> bool:
        return False


async def test_run_turn_raises_when_cancel_token_is_already_flipped() -> None:
    llm = StubLLMClient(script=[])
    loop = AgentLoop(llm, tools=[], max_steps=4, cancel_token=_AlwaysCancelledToken())

    context = ToolContext()
    with pytest.raises(TurnCancelledError):
        await loop.run_turn("hi", tool_context=context)


async def test_run_turn_proceeds_normally_when_cancel_token_is_never_flipped() -> None:
    llm = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="call-1", name="echo_tool", arguments={"text": "hi"}
            ),
            make_text_response("done"),
        ]
    )
    loop = AgentLoop(llm, tools=[EchoTool()], max_steps=4, cancel_token=_NeverCancelledToken())

    result = await loop.run_turn("echo hi", tool_context=ToolContext())

    assert len(result.steps) == 2


def test_cancel_token_setter_updates_the_active_token() -> None:
    llm = StubLLMClient(script=[])
    loop = AgentLoop(llm, tools=[], max_steps=4)
    assert loop.cancel_token is None

    token = _AlwaysCancelledToken()
    loop.cancel_token = token

    assert loop.cancel_token is token
