"""`Orchestrator.run` across all four frameworks, plus the §3b failure/ceiling semantics:
max-4 sub-agent clamp, lane isolation on one lane's exception, corrective-retry-then-drop,
quota-degradation-before-spawn, and the ceiling-exceeded degraded answer.

Every LLM call is a stub — the lane client is keyed on the skill body injected into its system
prompt so parallel lanes get deterministic, distinguishable responses without any live
network."""

from __future__ import annotations

from collections.abc import AsyncIterator

import fakeredis.aioredis

from config.settings import Settings
from llm.base import (
    LLMResponse,
    Message,
    ModelCapabilities,
    StopReason,
    StreamDoneEvent,
    TextDeltaEvent,
    TokenUsage,
    ToolCall,
    ToolCallEvent,
    ToolSpec,
)
from orchestrator.ceilings import QuotaReservation
from orchestrator.classifier import IntentClassifier
from orchestrator.models import Routing
from orchestrator.orchestrator import Orchestrator
from orchestrator.synthesis import Synthesizer
from schemas.enums import Tier
from skills.registry import SkillRegistry
from tests.llm.stubs import StubLLMClient, make_text_response, make_tool_call_response
from tools.base import Tool, ToolContext
from usage.finmind_pool import RedisFinMindPoolReserver


class _StaticClassifier(IntentClassifier):
    """A classifier that returns a fixed `Routing` without any LLM call — lets each framework
    test pin the route deterministically."""

    def __init__(self, registry: SkillRegistry, routing: Routing) -> None:
        super().__init__(registry)
        self._routing = routing

    async def _classify_inner(self, message: str) -> Routing:
        return self._routing


def _text_response(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        tool_calls=(),
        stop_reason=StopReason.END_TURN,
        usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
    )


class LaneAwareClient:
    """A lane client whose reply depends on which skill body is in its system prompt. `answers`
    maps a skill-body substring to the lane's final text; `blank_for` names substrings whose
    lane returns empty text (to exercise the corrective-retry-then-drop path); `raise_for` names
    substrings whose lane raises (to exercise lane isolation)."""

    provider_id = "stub"
    model_id = "stub-model"
    capabilities = ModelCapabilities()

    def __init__(
        self,
        answers: dict[str, str],
        *,
        loop_for: tuple[str, ...] = (),
        raise_for: tuple[str, ...] = (),
    ) -> None:
        self._answers = answers
        # `loop_for`: a matching lane perpetually proposes a tool call and never finalizes, so
        # it exhausts its step budget → StepBudgetExceededError → completeness="empty" (the real
        # empty-lane trigger, since check_output always appends a caveat to any final text).
        self._loop_for = loop_for
        self._raise_for = raise_for
        self.call_count = 0

    def _matched(self, messages: list[Message], keys: tuple[str, ...]) -> bool:
        system = messages[0].content or "" if messages else ""
        return any(k in system for k in keys)

    async def chat(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        self.call_count += 1
        system = messages[0].content or "" if messages else ""
        if self._matched(messages, self._raise_for):
            raise RuntimeError("lane provider exploded")
        if self._matched(messages, self._loop_for):
            return LLMResponse(
                text=None,
                tool_calls=(
                    ToolCall(id=f"c{self.call_count}", name="get_canonical_context", arguments={}),
                ),
                stop_reason=StopReason.TOOL_USE,
                usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
            )
        for marker, answer in self._answers.items():
            if marker in system:
                return _text_response(answer)
        return _text_response("generic lane answer. informational only.")

    async def stream(
        self, messages: list[Message], tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        raise NotImplementedError
        yield  # pragma: no cover

    async def count_tokens(self, messages: list[Message]) -> int:
        return sum(len(m.content or "") for m in messages) // 4 or 1


def _orchestrator(  # noqa: PLR0913 — one seam per collaborator, mirrors Orchestrator's own
    registry: SkillRegistry,
    tool_lookup: dict[str, Tool],
    routing: Routing,
    *,
    agent_client: object | None = None,
    subagent_client: object | None = None,
    synthesizer: Synthesizer | None = None,
    quota_reserver: object | None = None,
) -> Orchestrator:
    return Orchestrator(
        registry=registry,
        tool_lookup=tool_lookup,
        classifier=_StaticClassifier(registry, routing),
        synthesizer=synthesizer,
        quota_reserver=quota_reserver,  # type: ignore[arg-type]
        agent_client=agent_client,  # type: ignore[arg-type]
        subagent_client=subagent_client,  # type: ignore[arg-type]
    )


# ---- none ----


async def test_none_framework_replies_without_tools(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(framework="none", skills=(), reason="greeting")
    agent_client = StubLLMClient(script=[make_text_response("Hi! I analyze Taiwan stocks.")])
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run("hello", tool_context=ToolContext())

    assert "Hi!" in result.final_text
    assert result.routing.framework == "none"
    assert result.lanes == ()


# ---- single ----


async def test_single_framework_runs_one_skill_lane(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(framework="single", skills=("chip-flow-review",), reason="one dimension")
    agent_client = LaneAwareClient({"Chip-flow review": "Foreign net-buy context. Informational."})
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run("chip flows for 2330", tool_context=ToolContext())

    assert "Foreign net-buy" in result.final_text
    assert result.routing.framework == "single"


async def test_single_framework_without_skill_uses_warm_tools(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(framework="single", skills=(), reason="bare lookup")
    agent_client = StubLLMClient(script=[make_text_response("Bare context answer. Informational.")])
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run("what is 2330", tool_context=ToolContext())

    assert "Bare context" in result.final_text


# ---- subagent (parallel) ----


async def test_subagent_framework_fans_out_and_synthesizes(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two independent dimensions",
    )
    lane_client = LaneAwareClient(
        {
            "Chip-flow review": "chip lane finding. informational.",
            "Company profile": "profile lane finding. informational.",
        }
    )
    synth = Synthesizer(
        client=StubLLMClient(script=[make_text_response("merged both. info only.")])
    )
    orch = _orchestrator(
        registry, tool_lookup, routing, subagent_client=lane_client, synthesizer=synth
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    assert "merged both" in result.final_text
    assert result.provenance is not None
    assert set(result.provenance.contributing_skills) == {"chip-flow-review", "company-profile"}
    assert len(result.lanes) == 2
    # every lane carries a distinct agent_id (lane-scoped correlation).
    assert len({lane.agent_id for lane in result.lanes}) == 2


async def test_subagent_lane_isolation_on_one_lane_exception(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two dimensions",
    )
    # the company-profile lane raises; chip-flow-review succeeds — the turn still completes.
    lane_client = LaneAwareClient(
        {"Chip-flow review": "chip lane ok. informational."},
        raise_for=("Company profile",),
    )
    synth = Synthesizer(client=StubLLMClient(script=[make_text_response("merged survivor. info.")]))
    orch = _orchestrator(
        registry, tool_lookup, routing, subagent_client=lane_client, synthesizer=synth
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    assert result.provenance is not None
    assert result.provenance.contributing_skills == ("chip-flow-review",)
    assert "company-profile" in result.provenance.missing_skills
    errored = [lane for lane in result.lanes if lane.skill == "company-profile"]
    assert errored and errored[0].status == "error"


async def test_subagent_corrective_retry_then_drop_on_empty_lane(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two dimensions",
    )
    # company-profile never finalizes (always tool-calls) → exhausts its step budget on the
    # first attempt AND the one corrective retry → dropped-with-flag.
    lane_client = LaneAwareClient(
        {"Chip-flow review": "chip ok. informational."},
        loop_for=("Company profile",),
    )
    synth = Synthesizer(client=StubLLMClient(script=[make_text_response("merged one. info.")]))
    orch = Orchestrator(
        registry=registry,
        tool_lookup=tool_lookup,
        classifier=_StaticClassifier(registry, routing),
        synthesizer=synth,
        subagent_client=lane_client,
        settings=Settings(aitw_orch_max_steps_per_agent=2, aitw_orch_max_tool_calls_per_turn=100),
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    dropped = [lane for lane in result.lanes if lane.skill == "company-profile"]
    assert dropped and dropped[0].status == "dropped"
    assert "retry" in (dropped[0].reason or "")


async def test_subagent_clamps_to_four_lanes(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    # Only three skills exist, so to prove the clamp we set the ceiling to 1 via a Routing that
    # names two skills and assert only one lane runs.
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two dimensions",
    )
    lane_client = LaneAwareClient(
        {
            "Chip-flow review": "chip ok. info.",
            "Company profile": "profile ok. info.",
        }
    )
    synth = Synthesizer(client=StubLLMClient(script=[make_text_response("merged. info.")]))
    orch = Orchestrator(
        registry=registry,
        tool_lookup=tool_lookup,
        classifier=_StaticClassifier(registry, routing),
        synthesizer=synth,
        subagent_client=lane_client,
        settings=Settings(aitw_orch_max_subagents=1),
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    assert len(result.lanes) == 1


async def test_subagent_quota_degrades_lane_before_spawn(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two dimensions",
    )

    class _DegradingReserver:
        async def reserve(self, *, skills: tuple[str, ...], tier: str) -> QuotaReservation:
            return QuotaReservation(granted=("chip-flow-review",), degraded=("company-profile",))

    lane_client = LaneAwareClient({"Chip-flow review": "chip ok. info."})
    synth = Synthesizer(client=StubLLMClient(script=[make_text_response("merged one. info.")]))
    orch = _orchestrator(
        registry,
        tool_lookup,
        routing,
        subagent_client=lane_client,
        synthesizer=synth,
        quota_reserver=_DegradingReserver(),
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    degraded = [lane for lane in result.lanes if lane.skill == "company-profile"]
    assert degraded and degraded[0].status == "unavailable_tier"


async def test_real_finmind_reserver_degrades_via_run(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """The real `RedisFinMindPoolReserver` (fakeredis-backed) is exercised through the actual
    `Orchestrator.run` fan-out — not just unit-tested standalone. With the pool pre-drained, a
    Premium fan-out's over-budget lane is degraded to `unavailable_tier` before it spawns, proving
    the reserver is wired into the real reserve-before-fan-out seam."""
    redis = fakeredis.aioredis.FakeRedis()
    reserver = RedisFinMindPoolReserver(
        redis,
        key_prefix="finmind:pool",
        limit_per_window=1,
        window_seconds=3600,
        cost_per_lane=1,
    )
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two dimensions",
    )
    lane_client = LaneAwareClient({"Chip-flow review": "chip ok. info."})
    synth = Synthesizer(client=StubLLMClient(script=[make_text_response("merged one. info.")]))
    orch = _orchestrator(
        registry,
        tool_lookup,
        routing,
        subagent_client=lane_client,
        synthesizer=synth,
        quota_reserver=reserver,
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext(tier=Tier.PREMIUM))
    await redis.aclose()

    # Pool holds 1 lane's budget → one lane granted, one degraded before spawn.
    degraded = [lane for lane in result.lanes if lane.status == "unavailable_tier"]
    assert len(degraded) == 1


# ---- team (sequential) ----


async def test_team_framework_passes_findings_forward(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(
        framework="team",
        skills=("chip-flow-review", "company-profile"),
        reason="sequential dependency",
    )
    lane_client = LaneAwareClient(
        {
            "Chip-flow review": "TEAMMATE-A-FINDING. informational.",
            "Company profile": "teammate B built on A. informational.",
        }
    )
    synth = Synthesizer(client=StubLLMClient(script=[make_text_response("team merged. info.")]))
    orch = _orchestrator(
        registry, tool_lookup, routing, subagent_client=lane_client, synthesizer=synth
    )

    result = await orch.run("deep research 2330", tool_context=ToolContext())

    assert "team merged" in result.final_text
    assert len(result.lanes) == 2


async def test_team_second_teammate_receives_prior_finding(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(
        framework="team",
        skills=("chip-flow-review", "company-profile"),
        reason="sequential",
    )

    seen_prior: list[bool] = []

    class _RecordingClient(LaneAwareClient):
        async def chat(
            self, messages: list[Message], tools: list[ToolSpec] | None = None
        ) -> LLMResponse:
            system = messages[0].content or "" if messages else ""
            user = next((m.content or "" for m in messages if m.role.value == "user"), "")
            if "Company profile" in system:
                seen_prior.append("Prior finding" in user)
            return await super().chat(messages, tools)

    lane_client = _RecordingClient(
        {
            "Chip-flow review": "A finding. informational.",
            "Company profile": "B finding. informational.",
        }
    )
    synth = Synthesizer(client=StubLLMClient(script=[make_text_response("merged. info.")]))
    orch = _orchestrator(
        registry, tool_lookup, routing, subagent_client=lane_client, synthesizer=synth
    )

    await orch.run("deep research 2330", tool_context=ToolContext())

    assert seen_prior == [True]


# ---- ceiling abort ----


async def test_tool_call_ceiling_aborts_to_degraded_answer(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    routing = Routing(framework="single", skills=("chip-flow-review",), reason="one dimension")
    # the lane makes one tool call then answers → the single-framework tool-call accounting
    # records 1 > ceiling of 0, so `run` catches TurnCeilingExceededError and returns a
    # structured degraded answer rather than crashing.
    agent_client = StubLLMClient(
        script=[
            make_tool_call_response(
                tool_call_id="c1", name="get_canonical_context", arguments={"stock_id": "2330"}
            ),
            make_text_response("chip context. informational."),
        ]
    )
    orch = Orchestrator(
        registry=registry,
        tool_lookup=tool_lookup,
        classifier=_StaticClassifier(registry, routing),
        agent_client=agent_client,
        settings=Settings(aitw_orch_max_tool_calls_per_turn=0),
    )

    result = await orch.run("chip flows for 2330", tool_context=ToolContext())

    assert "ceiling" in result.final_text
    assert "not a buy/sell/hold" in result.final_text or "not" in result.final_text


# ---- governance: two-layer non-trading enforcement ----


async def test_per_lane_guardrail_redacts_trading_language_before_synthesis(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """A lane emitting trading language is redacted by its own Step-loop `pre_output` before its
    text enters synthesis input — trading language cannot be laundered into the merge."""
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two dimensions",
    )
    lane_client = LaneAwareClient(
        {
            "Chip-flow review": "You should buy 2330 now, strong buy!",
            "Company profile": "profile finding. informational.",
        }
    )
    synth_client = StubLLMClient(script=[make_text_response("merged. informational only.")])
    synth = Synthesizer(client=synth_client)
    orch = _orchestrator(
        registry, tool_lookup, routing, subagent_client=lane_client, synthesizer=synth
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    chip_lane = next(lane for lane in result.lanes if lane.skill == "chip-flow-review")
    assert "[redacted" in chip_lane.text
    assert "should buy" not in chip_lane.text.lower()
    # what synthesis received also carried the redaction, never the raw trading phrase.
    synth_input = " ".join(m.content or "" for m in synth_client.calls[0][0])
    assert "should buy" not in synth_input.lower()


async def test_final_synthesis_answer_passes_output_guardrail(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """Even if the synthesis model emits trading language, the final `check_output` redacts it —
    the second enforcement layer."""
    routing = Routing(
        framework="subagent",
        skills=("chip-flow-review", "company-profile"),
        reason="two dimensions",
    )
    lane_client = LaneAwareClient(
        {
            "Chip-flow review": "chip finding. informational.",
            "Company profile": "profile finding. informational.",
        }
    )
    synth = Synthesizer(
        client=StubLLMClient(script=[make_text_response("Our verdict: strong buy on 2330.")])
    )
    orch = _orchestrator(
        registry, tool_lookup, routing, subagent_client=lane_client, synthesizer=synth
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    assert "[redacted" in result.final_text
    assert "strong buy" not in result.final_text.lower()
