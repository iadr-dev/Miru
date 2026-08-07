"""`Synthesizer` — partial-lane success, conflicting lanes surfaced, synthesis-call-failure
concatenation fallback, all-lanes-failed, and the mandatory final guardrail on the merged
answer."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StreamDoneEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolSpec,
)
from orchestrator.models import LaneResult
from orchestrator.synthesis import NoSuccessfulLanesError, Synthesizer
from tests.llm.stubs import StubLLMClient, make_text_response


def _lane(skill: str, *, text: str = "", status: str = "ok") -> LaneResult:
    return LaneResult(
        agent_id=f"{skill}:1",
        skill=skill,
        text=text,
        status=status,  # type: ignore[arg-type]
    )


class _RaisingLLMClient:
    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        raise RuntimeError("synthesis provider exploded")

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        raise RuntimeError("boom")
        yield  # pragma: no cover

    async def count_tokens(self, messages: list[Message]) -> int:
        return 1


async def test_synthesis_merges_lanes_via_best_quality_model() -> None:
    client = StubLLMClient(
        script=[
            make_text_response(
                "Merged analytical context across both lanes. Not investment advice."
            )
        ]
    )
    synth = Synthesizer(client=client)

    text, provenance = await synth.synthesize(
        (_lane("chip-flow-review", text="foreign net buy"), _lane("company-profile", text="semi"))
    )

    assert "Merged" in text
    assert provenance.contributing_skills == ("chip-flow-review", "company-profile")
    assert provenance.missing_skills == ()
    assert provenance.degraded is False


async def test_synthesis_partial_declares_missing_dimensions() -> None:
    client = StubLLMClient(script=[make_text_response("One lane merged. Informational only.")])
    synth = Synthesizer(client=client)

    lanes = (
        _lane("chip-flow-review", text="foreign flows"),
        _lane("company-profile", status="error"),
    )
    text, provenance = await synth.synthesize(lanes)

    assert provenance.contributing_skills == ("chip-flow-review",)
    assert provenance.missing_skills == ("company-profile",)
    # the missing dimension is passed into the synthesis prompt (see the sent messages).
    sent = client.calls[0][0]
    assert any("company-profile" in (m.content or "") for m in sent)
    assert text


async def test_synthesis_fallback_concatenates_on_call_failure() -> None:
    synth = Synthesizer(client=_RaisingLLMClient())

    lanes = (
        _lane("chip-flow-review", text="foreign net buy 8 days"),
        _lane("company-profile", text="semiconductor, dividend announced 2026-07"),
    )
    text, provenance = await synth.synthesize(lanes)

    assert provenance.degraded is True
    assert "synthesis degraded" in text
    # both successful lanes preserved in the concatenation.
    assert "chip-flow-review" in text
    assert "company-profile" in text


async def test_synthesis_fallback_still_passes_output_guardrail() -> None:
    synth = Synthesizer(client=_RaisingLLMClient())

    lanes = (_lane("chip-flow-review", text="foreign flows over 20 days"),)
    text, _ = await synth.synthesize(lanes)

    # the non-trading caveat is appended by check_output even in the degraded fallback.
    assert "not a buy/sell/hold recommendation" in text


async def test_synthesis_raises_when_no_lane_succeeds() -> None:
    synth = Synthesizer(client=StubLLMClient(script=[]))

    lanes = (_lane("chip-flow-review", status="error"), _lane("company-profile", status="dropped"))

    with pytest.raises(NoSuccessfulLanesError):
        await synth.synthesize(lanes)


async def test_synthesis_conflicting_lanes_are_both_passed_to_the_model() -> None:
    client = StubLLMClient(
        script=[make_text_response("Both views surfaced with provenance. Informational only.")]
    )
    synth = Synthesizer(client=client)

    lanes = (
        _lane("chip-flow-review", text="foreign accumulating"),
        _lane("company-profile", text="foreign distributing"),
    )
    await synth.synthesize(lanes)

    sent = client.calls[0][0]
    joined = " ".join(m.content or "" for m in sent)
    assert "accumulating" in joined
    assert "distributing" in joined
