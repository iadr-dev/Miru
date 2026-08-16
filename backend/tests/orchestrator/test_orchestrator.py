"""`Orchestrator.run` across all four frameworks, plus the §3b failure/ceiling semantics:
max-4 sub-agent clamp, lane isolation on one lane's exception, corrective-retry-then-drop,
quota-degradation-before-spawn, and the ceiling-exceeded degraded answer.

Every LLM call is a stub — the lane client is keyed on the skill body injected into its system
prompt so parallel lanes get deterministic, distinguishable responses without any live
network."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest

from agent.guardrails import REDACTION_NOTICE
from config.settings import Settings
from events.bus import InMemoryEventBus
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

    async def _classify_inner(self, message: str, *, history: tuple[Message, ...] = ()) -> Routing:
        del message, history
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
        self.calls: list[tuple[list[Message], list[ToolSpec] | None]] = []

    def _matched(self, messages: list[Message], keys: tuple[str, ...]) -> bool:
        system = messages[0].content or "" if messages else ""
        return any(k in system for k in keys)

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> LLMResponse:
        del native_web_search
        self.call_count += 1
        self.calls.append((messages, tools))
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
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        *,
        native_web_search: bool = False,
    ) -> AsyncIterator[TextDeltaEvent | ToolCallEvent | StreamDoneEvent]:
        # `AgentLoop` now drives every Step via `stream()` (never `chat()` directly) — this
        # delegates to the same scripted `chat()` logic above rather than duplicating it, same
        # pattern as `tests/llm/stubs.py::StubLLMClient.stream`.
        response = await self.chat(messages, tools, native_web_search=native_web_search)
        if response.text:
            yield TextDeltaEvent(text=response.text)
        for call in response.tool_calls:
            yield ToolCallEvent(tool_call=call)
        yield StreamDoneEvent(stop_reason=response.stop_reason, usage=response.usage)

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


async def test_locale_defaults_to_zh_tw_in_system_prompt(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """Omitting `locale` on `Orchestrator.run` falls back to `zh-TW`, interpolated into the
    governance system prompt's language-matching rule."""
    routing = Routing(framework="none", skills=(), reason="greeting")
    agent_client = StubLLMClient(script=[make_text_response("Hi! Informational only.")])
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    await orch.run("hello", tool_context=ToolContext())

    system_prompt = agent_client.calls[0][0][0].content or ""
    assert "interface locale: zh-TW" in system_prompt


async def test_locale_is_interpolated_into_system_prompt_when_provided(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """An explicit `locale` (e.g. from `AgentQueryRequest.locale`) replaces the default in the
    governance system prompt's language-matching rule."""
    routing = Routing(framework="none", skills=(), reason="greeting")
    agent_client = StubLLMClient(script=[make_text_response("Hi! Informational only.")])
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    await orch.run("hello", tool_context=ToolContext(), locale="en")

    system_prompt = agent_client.calls[0][0][0].content or ""
    assert "interface locale: en" in system_prompt
    assert "interface locale: zh-TW" not in system_prompt


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


async def test_subagent_agent_spawned_activity_is_natural_language_not_the_raw_skill_id(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """`orchestrator.py`'s `agent.spawned` `activity` field is a natural-language label
    (`skills.activity_labels.skill_activity_label`) — never the literal
    `f"Running {skill_name}"` templating this replaced."""
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
    bus = InMemoryEventBus()

    spawned_activities: list[str] = []

    async def collect() -> None:
        async for event in bus.subscribe(["agent.spawned"]):
            activity = event.data.get("activity")
            assert isinstance(activity, str)
            spawned_activities.append(activity)
            if len(spawned_activities) == 2:
                return

    task = asyncio.create_task(collect())
    await asyncio.sleep(0)
    await orch.run(
        "analyze 2330", tool_context=ToolContext(), event_bus=bus, turn_id="turn-1", locale="zh-TW"
    )
    await task

    assert "Running chip-flow-review" not in spawned_activities
    assert "Running company-profile" not in spawned_activities
    assert "正在檢視籌碼流向" in spawned_activities
    assert "正在查詢個股基本資料" in spawned_activities


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
        settings=Settings(orch_max_steps_per_agent=2, orch_max_tool_calls_per_turn=100),
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
        settings=Settings(orch_max_subagents=1),
    )

    result = await orch.run("analyze 2330", tool_context=ToolContext())

    assert len(result.lanes) == 1
    assert result.provenance is not None
    assert result.provenance.dropped_skill_count == 1
    assert "additional analytical angle" in result.final_text


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
            self,
            messages: list[Message],
            tools: list[ToolSpec] | None = None,
            *,
            native_web_search: bool = False,
        ) -> LLMResponse:
            system = messages[0].content or "" if messages else ""
            user = next((m.content or "" for m in messages if m.role.value == "user"), "")
            if "Company profile" in system:
                seen_prior.append("Prior finding" in user)
            return await super().chat(messages, tools, native_web_search=native_web_search)

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
        settings=Settings(orch_max_tool_calls_per_turn=0),
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
    assert REDACTION_NOTICE in chip_lane.text
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

    assert REDACTION_NOTICE in result.final_text
    assert "strong buy" not in result.final_text.lower()


# ---- per-thread model_override (none/single framework only) ----


async def test_model_override_used_for_none_framework(
    registry: SkillRegistry, tool_lookup: dict[str, Tool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resolvable `model_override` string substitutes the AGENT-role client on the `none`
    framework path — `resolve_role_with_override` should call `llm.registry.build_client` with
    the parsed `(provider, model)` instead of `resolve_role(ModelRole.AGENT, ...)`."""
    routing = Routing(framework="none", skills=(), reason="greeting")
    override_client = StubLLMClient(script=[make_text_response("Hi from the overridden model.")])
    captured: list[tuple[str, str]] = []

    def _fake_build_client(provider_id: str, model_id: str, settings: object) -> object:
        captured.append((provider_id, model_id))
        return override_client

    def _fail_resolve_role(role: object, settings: object | None = None) -> object:
        raise AssertionError("resolve_role should not be called when model_override resolves")

    monkeypatch.setattr("llm.registry.build_client", _fake_build_client)
    monkeypatch.setattr("llm.registry.resolve_role", _fail_resolve_role)

    orch = _orchestrator(registry, tool_lookup, routing)

    result = await orch.run(
        "hello",
        tool_context=ToolContext(),
        model_override="anthropic/claude-opus-4-8",
    )

    assert captured == [("anthropic", "claude-opus-4-8")]
    assert "Hi from the overridden model." in result.final_text


async def test_model_override_malformed_degrades_to_agent_role(
    registry: SkillRegistry, tool_lookup: dict[str, Tool], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A malformed/unresolvable `model_override` never fails the turn — it degrades to the
    normal `resolve_role(ModelRole.AGENT, ...)` behavior, so the turn still completes with a
    normal answer."""
    routing = Routing(framework="none", skills=(), reason="greeting")
    fallback_client = StubLLMClient(script=[make_text_response("Fallback answer. Informational.")])

    def _fail_build_client(provider_id: str, model_id: str, settings: object) -> object:
        raise AssertionError("build_client should not be reached for a malformed override")

    def _fake_resolve_role(role: object, settings: object | None = None) -> object:
        return fallback_client

    monkeypatch.setattr("llm.registry.build_client", _fail_build_client)
    monkeypatch.setattr("llm.registry.resolve_role", _fake_resolve_role)

    orch = _orchestrator(registry, tool_lookup, routing)

    result = await orch.run(
        "hello",
        tool_context=ToolContext(),
        model_override="not-a-valid-spec",
    )

    assert "Fallback answer" in result.final_text
    assert result.final_text  # the turn completed normally, never raised


# ---- per-turn web_search toggle (none/single framework only) ----


def _tool_names_from_call(client: StubLLMClient) -> set[str]:
    """The tool names sent to the LLM on its (sole, scripted) call — what the model actually
    saw as callable, not just what the orchestrator happened to resolve internally."""
    assert client.calls, "client was never called"
    _, tools = client.calls[0]
    return {tool.name for tool in (tools or ())}


async def test_web_search_enabled_grants_tools_on_none_framework(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """`web_search=True` on a `none`-framework turn adds `web_search`/`fetch_url` to the tool
    list handed to the `AgentLoop` — the model actually sees them as callable."""
    routing = Routing(framework="none", skills=(), reason="greeting")
    agent_client = StubLLMClient(script=[make_text_response("Hi! Informational only.")])
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run("hello", tool_context=ToolContext(), web_search_enabled=True)

    assert result.final_text
    assert {"web_search", "fetch_url"} <= _tool_names_from_call(agent_client)


async def test_web_search_enabled_grants_tools_on_single_framework(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """`web_search=True` on a `single`-framework turn appends `web_search`/`fetch_url` to the
    matched skill's own allowlisted tools, rather than replacing them."""
    routing = Routing(framework="single", skills=("chip-flow-review",), reason="one dimension")
    agent_client = StubLLMClient(
        script=[make_text_response("Foreign net-buy context. Informational.")]
    )
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run(
        "chip flows for 2330", tool_context=ToolContext(), web_search_enabled=True
    )

    assert result.final_text
    tool_names = _tool_names_from_call(agent_client)
    assert {"web_search", "fetch_url"} <= tool_names
    # The skill's own tools are still present — additive, not a replacement.
    assert tool_names - {"web_search", "fetch_url"}


async def test_web_search_disabled_by_default_omits_tools(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """Omitting `web_search` (default `False`) never grants the web-access tools — the toggle
    is strictly opt-in, matching `AgentQueryRequest.web_search`'s own default."""
    routing = Routing(framework="single", skills=("chip-flow-review",), reason="one dimension")
    agent_client = StubLLMClient(
        script=[make_text_response("Foreign net-buy context. Informational.")]
    )
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run("chip flows for 2330", tool_context=ToolContext())

    assert result.final_text
    tool_names = _tool_names_from_call(agent_client)
    assert "web_search" not in tool_names
    assert "fetch_url" not in tool_names


async def test_web_search_enabled_uses_native_mechanism_instead_of_brave_tools(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """When the resolved client's own capability says it has a native web-search mechanism,
    the orchestrator must pass `native_web_search=True` into the `chat()` call and never
    append the Brave-backed `web_search`/`fetch_url` tools — the two paths are mutually
    exclusive for one turn (see `orchestrator._maybe_add_web_search`)."""
    routing = Routing(framework="none", skills=(), reason="greeting")
    agent_client = StubLLMClient(
        script=[make_text_response("Hi! Informational only.")],
        capabilities=ModelCapabilities(native_web_search=True),
    )
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run("hello", tool_context=ToolContext(), web_search_enabled=True)

    assert result.final_text
    assert "web_search" not in _tool_names_from_call(agent_client)
    assert agent_client.native_web_search_calls == [True]


async def test_web_search_enabled_native_client_still_false_when_toggle_off(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """A native-web-search-capable client must not get `native_web_search=True` unless this
    turn's own toggle was on — the capability alone never forces it."""
    routing = Routing(framework="none", skills=(), reason="greeting")
    agent_client = StubLLMClient(
        script=[make_text_response("Hi! Informational only.")],
        capabilities=ModelCapabilities(native_web_search=True),
    )
    orch = _orchestrator(registry, tool_lookup, routing, agent_client=agent_client)

    result = await orch.run("hello", tool_context=ToolContext())

    assert result.final_text
    assert agent_client.native_web_search_calls == [False]


async def test_web_search_enabled_not_granted_to_subagent_fanout_lanes(
    registry: SkillRegistry, tool_lookup: dict[str, Tool]
) -> None:
    """A `subagent`-framework turn with `web_search=True` never grants the tools to its
    fan-out lanes — the SUBAGENT-role carve-out mirroring `model_override`'s own."""
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

    result = await orch.run("analyze 2330", tool_context=ToolContext(), web_search_enabled=True)

    assert "merged both" in result.final_text
    for _messages, tools in lane_client.calls:
        tool_names = {tool.name for tool in (tools or ())}
        assert "web_search" not in tool_names
        assert "fetch_url" not in tool_names
