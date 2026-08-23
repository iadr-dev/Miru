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
    # the missing dimension is passed into the synthesis prompt as its human-readable label
    # (never the raw skill id — that would leak an internal identifier into the model's own
    # merge input, and from there potentially into the user-facing answer).
    sent = client.calls[0][0]
    assert any("個股基本資料" in (m.content or "") for m in sent)
    assert not any("company-profile" in (m.content or "") for m in sent)
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
    # both successful lanes preserved in the concatenation, by their human-readable label —
    # never the raw internal skill id, even in the degraded fallback path.
    assert "籌碼流向" in text
    assert "個股基本資料" in text
    assert "chip-flow-review" not in text
    assert "company-profile" not in text


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


async def test_synthesis_model_override_used_when_no_client_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread's `model_override` reaches the SYNTHESIS role too, not just AGENT — this is the
    pass that writes the final answer a fan-out turn's user actually reads."""
    override_client = StubLLMClient(
        script=[make_text_response("Merged via the overridden model. Informational only.")]
    )
    captured: list[tuple[str, str]] = []

    def _fake_build_client(provider_id: str, model_id: str, settings: object) -> object:
        captured.append((provider_id, model_id))
        return override_client

    monkeypatch.setattr("llm.registry.build_client", _fake_build_client)
    synth = Synthesizer()

    lanes = (_lane("chip-flow-review", text="foreign net buy"),)
    text, _ = await synth.synthesize(lanes, model_override="anthropic/claude-opus-4-8")

    assert captured == [("anthropic", "claude-opus-4-8")]
    assert "Merged via the overridden model" in text


async def test_synthesis_uses_zh_tw_system_prompt_by_default() -> None:
    """Without an explicit `locale`, the synthesis system prompt is zh-TW — the default
    interface locale — so a fan-out turn's merged answer isn't silently produced in English
    regardless of what language the user actually asked in (governance rule 8)."""
    client = StubLLMClient(script=[make_text_response("已合併兩個分析路徑的結果。僅供參考。")])
    synth = Synthesizer(client=client)

    await synth.synthesize((_lane("chip-flow-review", text="外資買超"),))

    sent = client.calls[0][0]
    system_message = next(m for m in sent if m.role.value == "system")
    assert "繁體中文" in (system_message.content or "")


async def test_synthesis_honors_an_explicit_locale() -> None:
    client = StubLLMClient(script=[make_text_response("Merged. Informational only.")])
    synth = Synthesizer(client=client)

    await synth.synthesize((_lane("chip-flow-review", text="foreign net buy"),), locale="en")

    sent = client.calls[0][0]
    system_message = next(m for m in sent if m.role.value == "system")
    assert "entirely in English" in (system_message.content or "")
    assert "繁體中文" not in (system_message.content or "")


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
