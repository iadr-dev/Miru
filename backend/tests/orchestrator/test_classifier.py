"""`IntentClassifier` — LLM-verdict parsing, the deterministic keyword fallback on failure,
skill-name clamping, and the journaled `route.decided` event."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from events.bus import InMemoryEventBus
from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
    ToolCallEvent,
    ToolSpec,
)
from orchestrator.classifier import IntentClassifier
from skills.registry import SkillRegistry
from tests.llm.stubs import StubLLMClient, make_text_response


def _routing_response(payload: str) -> LLMResponse:
    return LLMResponse(
        text=payload,
        tool_calls=(),
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
    )


class _RaisingLLMClient:
    """A stub that raises on `chat` — exercises the classifier's failure-fallback path."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        raise RuntimeError("provider exploded")

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover

    async def count_tokens(self, messages: list[Message]) -> int:
        return 1


async def test_classifier_parses_subagent_verdict(registry: SkillRegistry) -> None:
    client = StubLLMClient(
        script=[
            _routing_response(
                '{"framework": "subagent", '
                '"skills": ["chip-flow-review", "company-profile"], '
                '"reason": "two independent dimensions"}'
            )
        ]
    )
    classifier = IntentClassifier(registry, client=client)

    routing = await classifier.classify("compare chip flows and profile for 2330")

    assert routing.framework == "subagent"
    assert routing.skills == ("chip-flow-review", "company-profile")
    assert routing.reason


async def test_classifier_none_forces_empty_skills(registry: SkillRegistry) -> None:
    client = StubLLMClient(
        script=[
            _routing_response(
                '{"framework": "none", "skills": ["chip-flow-review"], "reason": "greeting"}'
            )
        ]
    )
    classifier = IntentClassifier(registry, client=client)

    routing = await classifier.classify("hello there")

    assert routing.framework == "none"
    assert routing.skills == ()


async def test_classifier_drops_unknown_skill_names(registry: SkillRegistry) -> None:
    client = StubLLMClient(
        script=[
            _routing_response(
                '{"framework": "single", "skills": ["chip-flow-review", "not-a-real-skill"], '
                '"reason": "x"}'
            )
        ]
    )
    classifier = IntentClassifier(registry, client=client)

    routing = await classifier.classify("chip flows for 2330")

    assert routing.skills == ("chip-flow-review",)


async def test_classifier_clamps_to_four_skills(registry: SkillRegistry) -> None:
    # duplicate valid names to exceed the ceiling — clamp keeps at most four.
    client = StubLLMClient(
        script=[
            _routing_response(
                '{"framework": "subagent", "skills": '
                '["chip-flow-review","company-profile","active-etf-flow","chip-flow-review",'
                '"company-profile"], "reason": "x"}'
            )
        ]
    )
    classifier = IntentClassifier(registry, client=client)

    routing = await classifier.classify("everything")

    assert len(routing.skills) <= 4


async def test_classifier_falls_back_to_keywords_on_call_failure(
    registry: SkillRegistry,
) -> None:
    classifier = IntentClassifier(registry, client=_RaisingLLMClient())

    # 法人/籌碼 are chip-flow-review triggers — the keyword fallback should pin that skill.
    routing = await classifier.classify("外資籌碼怎麼樣")

    assert routing.framework == "single"
    assert routing.skills == ("chip-flow-review",)
    assert "fallback" in routing.reason


async def test_classifier_falls_back_on_unparseable_json(registry: SkillRegistry) -> None:
    client = StubLLMClient(script=[make_text_response("not json at all, sorry")])
    classifier = IntentClassifier(registry, client=client)

    routing = await classifier.classify("公司概況 for 2330")

    assert routing.framework == "single"
    # company-profile triggers include 公司概況.
    assert routing.skills == ("company-profile",)


async def test_classifier_fallback_single_with_no_keyword_match(registry: SkillRegistry) -> None:
    classifier = IntentClassifier(registry, client=_RaisingLLMClient())

    routing = await classifier.classify("zzz nothing matches here")

    assert routing.framework == "single"
    assert routing.skills == ()


async def test_classifier_emits_route_decided_event(registry: SkillRegistry) -> None:
    bus = InMemoryEventBus()
    published: list[str] = []

    async def _drain() -> None:
        async for event in bus.subscribe(["route.decided"]):
            published.append(event.kind)
            break

    client = StubLLMClient(
        script=[_routing_response('{"framework": "single", "skills": [], "reason": "lookup"}')]
    )
    classifier = IntentClassifier(registry, client=client)

    task = asyncio.create_task(_drain())
    await asyncio.sleep(0)
    await classifier.classify("what is 2330", event_bus=bus, turn_id="t1")
    await asyncio.wait_for(task, timeout=1.0)

    assert published == ["route.decided"]
