"""The orchestrator: classify a turn, run the chosen framework, synthesize, enforce ceilings.

Wires the four pieces built alongside it — `IntentClassifier`, the per-lane `AgentLoop` fan-out
(runtime, below this layer), `Synthesizer`, and the `TurnBudget`/`QuotaReserver` ceilings — into
one `Orchestrator.run` entrypoint a future turn-lifecycle caller invokes instead of `AgentLoop`
directly.

Framework mapping:
- `none`  — direct reply from the system prompt, no tools, no skills.
- `single` — one `AgentLoop` with the matched skill's tool allowlist (or the planner's warm
  context/health tools when no skill matched).
- `subagent` — parallel fan-out: one `AgentLoop` per skill under
  `asyncio.gather(..., return_exceptions=True)` for lane isolation, each with a scoped context
  slice and its skill's allowlist, then synthesis.
- `team` — sequential fan-out: teammates run in order, each receiving prior teammates'
  structured `Finding` records (never raw transcript), then synthesis.

Governance: each lane's output is guardrail-checked inside its own Step loop before it enters
synthesis, and the synthesized answer is guardrail-checked again (two layers). The routing
decision, each lane's `agent_id`, and synthesis provenance are journaled/evented.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

import structlog

from agent.hooks import (
    AnswerDeltaHook,
    PostToolHook,
    PreLLMHook,
    PreToolHook,
    SteeringSource,
    ThinkingHook,
    WebSearchHook,
)
from agent.loop import AgentLoop, StepBudgetExceededError, TurnResult
from agent.prompts import PromptLayers, default_prompt_layers
from config.settings import Settings, get_settings
from events.bus import EventBus
from events.hooks import RecallBlockProvider
from events.kinds import EventKind
from events.usage import UsageAccumulator
from llm.base import ImagePart, LLMClient, Message, WebSearchSource
from llm.registry import (
    ModelRole,
    ProviderNotConfiguredError,
    resolve_role,
    resolve_role_with_override,
)
from orchestrator.ceilings import (
    NoQuotaReserver,
    QuotaReservation,
    QuotaReserver,
    TurnBudget,
    TurnCeilingExceededError,
)
from orchestrator.classifier import IntentClassifier
from orchestrator.models import (
    Finding,
    LaneResult,
    OrchestratorResult,
    RichCard,
    Routing,
    SynthesisProvenance,
)
from orchestrator.synthesis import NoSuccessfulLanesError, Synthesizer
from personality.registry import PersonalityRegistry, default_registry
from schemas.enums import DataClass
from skills.activity_labels import skill_activity_label
from skills.models import Skill
from skills.registry import SkillRegistry
from tools.base import Tool, ToolContext

logger = structlog.get_logger(__name__)

_ROOT_AGENT_ID = "root"
_WARM_TOOLS: tuple[str, ...] = (
    "get_canonical_context",
    "get_source_health",
    "propose_alert",
    "propose_memory",
    "propose_schedule",
    "propose_watchlist_change",
)
_DEGRADED_ALL_LANES = (
    "All analysis lanes were unavailable for this request, so no market context could be "
    "compiled. This is informational only and not a buy/sell/hold recommendation; please try "
    "again shortly."
)


def _append_dropped_notice(text: str, dropped_skill_count: int) -> str:
    """Surfaces a fan-out clamp rather than silently absorbing it — appended once per turn when
    the classifier identified more analytical angles than actually ran (either the `_MAX_SKILLS`
    routing-parse backstop or `TurnBudget.clamp_subagents`'s per-turn ceiling clamped the list)."""
    if dropped_skill_count <= 0:
        return text
    notice = (
        f"（另有 {dropped_skill_count} 個分析角度因單次請求上限未執行 / "
        f"{dropped_skill_count} additional analytical angle"
        f"{'s' if dropped_skill_count != 1 else ''} were identified but not run due to a "
        "per-turn limit.）"
    )
    return f"{text}\n\n{notice}"


def _tools_for(skill: Skill, tool_lookup: dict[str, Tool]) -> list[Tool]:
    """The skill's tool allowlist resolved to concrete `Tool`s. Only allowlisted tools are ever
    handed to the lane's `AgentLoop`, so a call outside the allowlist is unreachable — the loop
    has no such tool to dispatch (the registry's `enforce_allowlist` guarantee, realized by
    construction of the lane's tool set)."""
    return [tool_lookup[name] for name in skill.frontmatter.tools if name in tool_lookup]


def _warm_tools(tool_lookup: dict[str, Tool]) -> list[Tool]:
    return [tool_lookup[name] for name in _WARM_TOOLS if name in tool_lookup]


def _has_sponsor_tool(tools: list[Tool]) -> bool:
    return any(tool.data_class is DataClass.SPONSOR for tool in tools)


_WEB_SEARCH_TOOLS: tuple[str, ...] = ("web_search", "fetch_url")


def _maybe_add_web_search(
    tools: list[Tool],
    tool_lookup: dict[str, Tool],
    client: LLMClient,
    *,
    web_search_enabled: bool,
) -> tuple[list[Tool], bool]:
    """Decide this turn's web-search posture, branching on the resolved LLM client's own
    capability: when the client's native web-search mechanism is available
    (`client.capabilities.native_web_search`), grant that instead of the Brave-backed
    `web_search`/`fetch_url` tools — the two are never both active for one turn, since the
    native path already gives the model the same grounding/search ability the Brave tools
    would, just server-side and without the extra tool-call round trip. Returns the (possibly
    unchanged) tool list plus whether `native_web_search=True` should be passed into this
    turn's `chat()`/`stream()` call.

    Falls back to the additive Brave-tool behavior when the client has no native mechanism —
    looked up from the same `tool_lookup` dict `_tools_for`/`_warm_tools` resolve from, so a
    deployment that hasn't registered the web-search tools (no Brave key configured) silently
    grants nothing rather than raising. Called for the `none`/`single` root agents and, when the
    user switched web search on, for every fan-out lane too (the toggle is the user's explicit
    opt-in; `model_override` keeps its SUBAGENT carve-out)."""
    if not web_search_enabled:
        return tools, False
    if client.capabilities.native_web_search:
        return tools, True
    extra = [tool_lookup[name] for name in _WEB_SEARCH_TOOLS if name in tool_lookup]
    return [*tools, *extra], False


@dataclasses.dataclass(frozen=True)
class PersonalityContext:
    """The turn's personality-resolution inputs, threaded in from whatever caller already holds
    the user/thread context (the API router's authenticated `User` + loaded `Thread`; a
    scheduled job's loaded `User`, which has no thread so `thread_override` is `None`).

    `turn_override` has no producer yet — no API surface lets a single turn pin a one-off
    preset (Fix B's related finding) — so it is always `None` until that surface exists; the
    field stays here so `PersonalityRegistry.resolve`'s full turn -> thread -> user precedence is
    honored the moment one lands, with no further orchestrator change required.
    """

    turn_override: str | None = None
    thread_override: str | None = None
    user_preference: str | None = None
    # The equipped pet's tone hint (None unless the user opted in); the last styling layer.
    pet_hint: str | None = None


@dataclasses.dataclass(frozen=True)
class StyleFragments:
    """The styling layers resolved for one turn: the 語氣 fragment and the optional pet hint."""

    personality: str
    pet_hint: str | None = None


def _resolve_personality_fragment(
    registry: PersonalityRegistry, personality: PersonalityContext | None
) -> StyleFragments:
    ctx = personality or PersonalityContext()
    fragment = registry.resolve_fragment(
        turn_override=ctx.turn_override,
        thread_override=ctx.thread_override,
        user_preference=ctx.user_preference,
    )
    return StyleFragments(personality=fragment, pet_hint=ctx.pet_hint)


def _skill_prompt_layers(
    skill: Skill | None, personality_fragment: StyleFragments, locale: str
) -> PromptLayers:
    base = default_prompt_layers(locale)
    layers = dataclasses.replace(
        base, personality=personality_fragment.personality, pet_hint=personality_fragment.pet_hint
    )
    if skill is None:
        return layers
    return dataclasses.replace(layers, skill_body=skill.body)


def _lane_from_turn(turn: TurnResult, *, agent_id: str, skill: str) -> LaneResult:
    text = turn.final_text.strip()
    if not text:
        return LaneResult(
            agent_id=agent_id,
            skill=skill,
            status="degraded",
            completeness="empty",
            confidence=0.0,
            reason="lane produced no final content",
            rich_cards=turn.rich_cards,
            web_search_sources=turn.web_search_sources,
            tool_calls=turn.tool_calls,
            thinking_text=turn.thinking_text,
        )
    return LaneResult(
        agent_id=agent_id,
        skill=skill,
        text=text,
        status="ok",
        rich_cards=turn.rich_cards,
        web_search_sources=turn.web_search_sources,
        tool_calls=turn.tool_calls,
        thinking_text=turn.thinking_text,
    )


def _root_lane_from_turn(turn: TurnResult, *, skill: str) -> LaneResult:
    """A synthetic `LaneResult` for the `single` framework's one root-level loop (no fan-out, no
    `_run_one_lane` spawn) — exists purely so its tool-call/thinking trace has somewhere to land
    on `OrchestratorResult.lanes` for persistence/process-trace purposes; synthesis itself never
    reads a `single` turn's lanes (its answer is `turn.final_text` directly)."""
    return LaneResult(
        agent_id=_ROOT_AGENT_ID,
        skill=skill,
        text=turn.final_text.strip(),
        status="ok",
        rich_cards=turn.rich_cards,
        web_search_sources=turn.web_search_sources,
        tool_calls=turn.tool_calls,
        thinking_text=turn.thinking_text,
    )


def _finding_from_lane(lane: LaneResult) -> Finding:
    """Reduce a completed lane to the structured `Finding` a downstream teammate receives —
    decision (the lane's conclusion text, bounded) + provenance, never the raw transcript."""
    decision = lane.text.strip() or f"{lane.skill}: no usable finding"
    return Finding(
        skill=lane.skill,
        agent_id=lane.agent_id,
        decision=decision[:2000],
        cause=lane.reason or "",
    )


def _lane_cards(lanes: tuple[LaneResult, ...]) -> tuple[RichCard, ...]:
    """Flatten every lane's rich cards into one turn-level tuple, in lane order — the durable
    record of what the fan-out surfaced, mirroring the live per-lane `rich_card` events."""
    return tuple(card for lane in lanes for card in lane.rich_cards)


def _lane_web_search_sources(lanes: tuple[LaneResult, ...]) -> tuple[WebSearchSource, ...]:
    """Flatten every lane's web-search sources into one turn-level tuple, in lane order —
    mirrors `_lane_cards`'s own flattening, the durable record `api.turn_driver._persist_answer`
    turns into a `WebSearchSourcesBlock`."""
    return tuple(source for lane in lanes for source in lane.web_search_sources)


def _render_findings(findings: tuple[Finding, ...]) -> str:
    blocks = [f"### Prior finding from {f.skill}\n{f.decision}" for f in findings]
    return (
        "Prior teammates' structured findings (treat as established context, build on them):\n\n"
        + "\n\n".join(blocks)
    )


class Orchestrator:
    """Classify-then-route entrypoint. Construct once per application (stateless across turns);
    `run` is called per turn with the user message + tool context."""

    def __init__(  # noqa: PLR0913 — one seam per collaborator; each is independently swappable
        self,
        *,
        registry: SkillRegistry,
        tool_lookup: dict[str, Tool],
        classifier: IntentClassifier | None = None,
        synthesizer: Synthesizer | None = None,
        quota_reserver: QuotaReserver | None = None,
        agent_client: LLMClient | None = None,
        subagent_client: LLMClient | None = None,
        settings: Settings | None = None,
        personality_registry: PersonalityRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._tool_lookup = tool_lookup
        self._settings = settings or get_settings()
        self._classifier = classifier or IntentClassifier(registry)
        self._synthesizer = synthesizer or Synthesizer()
        # Constructed once (mirroring every other collaborator here) rather than per-call: the
        # registry loads + validates every preset file from disk, so a per-turn instantiation
        # would re-parse YAML on every request for no benefit — the resolved presets are
        # immutable and shared across turns.
        self._personality_registry = personality_registry or default_registry()
        # Config-selected FinMind pool reserver, mirroring the event-bus factory: an explicitly
        # injected reserver always wins (tests); otherwise the Redis Sponsor-pool reserver is
        # selected when the quota backend is Redis, else the permissive default. Imported locally
        # so the module load order stays acyclic across the interaction layer.
        if quota_reserver is not None:
            self._quota_reserver: QuotaReserver = quota_reserver
        else:
            from usage.finmind_pool import build_quota_reserver  # noqa: PLC0415 — same-layer wiring

            self._quota_reserver = build_quota_reserver(self._settings) or NoQuotaReserver()
        # Injected clients override role resolution — the seam tests use to avoid live
        # providers. When omitted, each role resolves through `llm/registry.py` at use time
        # (single/none = strong AGENT role; lanes = cheap/fast SUBAGENT role), never hardcoded.
        self._agent_client = agent_client
        self._subagent_client = subagent_client

    async def run(  # noqa: PLR0913 — one field per turn-start dimension
        self,
        user_message: str,
        *,
        tool_context: ToolContext,
        event_bus: EventBus | None = None,
        turn_id: str | None = None,
        personality: PersonalityContext | None = None,
        steering_source: SteeringSource | None = None,
        locale: str = "zh-TW",
        model_override: str | None = None,
        web_search_enabled: bool = False,
        images: tuple[ImagePart, ...] = (),
        history: tuple[Message, ...] = (),
        recall_provider: RecallBlockProvider | None = None,
    ) -> OrchestratorResult:
        """`steering_source` is only meaningful for `single` (the interactive root-agent lane a
        live `POST .../steer` call actually targets) — the fan-out frameworks' sub-agent lanes
        are independent, short-lived, and not individually steerable by a client that only knows
        the turn, not any one lane's `agent_id`; wiring it there is future scope, not silently
        dropped functionality today.

        `model_override`/`images` are per-turn knobs consulted only by the `none`/`single`
        framework paths (the interactive root-agent lane) — fan-out lanes (`subagent`/`team`)
        never see them, mirroring the SUBAGENT-role carve-out `_resolve_client` already enforces
        for `model_override` (an attached image is part of the user's own turn, not something a
        fan-out sub-agent lane independently receives). `web_search_enabled` is the exception: it
        is the user's explicit opt-in, so every fan-out lane gets web search too and the lane
        sources are merged into the turn's `web_search_sources`.

        `history` is prior turns from the same thread (oldest first, caller-supplied — the
        orchestrator has no `conversation` access per the dependency rule, so it never fetches
        this itself). Threaded to the classifier as a compact rendered summary (`_render_history`)
        so a follow-up like "what about its dividend history?" can resolve its missing subject,
        and to every framework's `AgentLoop`/lane so the skill's own LLM call sees the same
        context, not just the routing decision.

        `recall_provider` is a zero-arg thunk (bound to this turn's user/thread/query by the
        caller) that renders the long-term-memory block injected as a `pre_llm` SYSTEM message.
        Like `web_search_enabled`/`images` it is consulted only by the `none`/`single` root-agent
        paths — fan-out sub-agent lanes never receive it (the memory block is background for the
        user-facing reply agent, not something each independent lane re-injects and re-guardrails
        N times; the fan-out synthesis pass is a separate LLM seam out of `_build_loop`'s scope)."""
        routing = await self._classifier.classify(
            user_message, event_bus=event_bus, turn_id=turn_id, history=history
        )
        budget = TurnBudget.from_settings(self._settings)
        usage = UsageAccumulator(turn_id or "")
        fragment = _resolve_personality_fragment(self._personality_registry, personality)
        try:
            if routing.framework == "none":
                result = await self._run_none(
                    user_message,
                    routing,
                    tool_context,
                    usage,
                    fragment,
                    model_override,
                    event_bus=event_bus,
                    turn_id=turn_id,
                    web_search_enabled=web_search_enabled,
                    locale=locale,
                    images=images,
                    history=history,
                    recall_provider=recall_provider,
                )
            elif routing.framework == "single":
                result = await self._run_single(
                    user_message=user_message,
                    routing=routing,
                    tool_context=tool_context,
                    budget=budget,
                    event_bus=event_bus,
                    turn_id=turn_id,
                    usage=usage,
                    personality_fragment=fragment,
                    steering_source=steering_source,
                    locale=locale,
                    model_override=model_override,
                    web_search_enabled=web_search_enabled,
                    images=images,
                    history=history,
                    recall_provider=recall_provider,
                )
            elif routing.framework == "subagent":
                result = await self._run_subagent(
                    user_message=user_message,
                    routing=routing,
                    tool_context=tool_context,
                    budget=budget,
                    event_bus=event_bus,
                    turn_id=turn_id,
                    usage=usage,
                    personality_fragment=fragment,
                    locale=locale,
                    model_override=model_override,
                    history=history,
                    web_search_enabled=web_search_enabled,
                )
            else:
                result = await self._run_team(
                    user_message=user_message,
                    routing=routing,
                    tool_context=tool_context,
                    budget=budget,
                    event_bus=event_bus,
                    turn_id=turn_id,
                    usage=usage,
                    personality_fragment=fragment,
                    locale=locale,
                    model_override=model_override,
                    history=history,
                    web_search_enabled=web_search_enabled,
                )
        except TurnCeilingExceededError as exc:
            logger.warning(
                "orch_turn_ceiling_exceeded",
                turn_id=turn_id,
                dimension=exc.dimension,
                limit=exc.limit,
            )
            return self._finalize(self._degraded_ceiling_result(routing, exc), usage, event_bus)
        return self._finalize(result, usage, event_bus)

    async def run_skill(  # noqa: PLR0913 — one field per turn-start dimension
        self,
        skill_ref: str,
        user_message: str,
        *,
        tool_context: ToolContext,
        event_bus: EventBus | None = None,
        turn_id: str | None = None,
        personality: PersonalityContext | None = None,
        locale: str = "zh-TW",
    ) -> OrchestratorResult:
        """Run one already-chosen skill, skipping the intent classifier entirely.

        A caller that already knows which skill to run (a scheduled job names its skill) uses
        this instead of `run`: it avoids the extra classifier LLM call and the risk of
        re-routing to a different framework, while reusing the exact single-lane machinery —
        same `AgentLoop`, same per-agent budget, and the same mandatory `pre_output` guardrail
        that fires on every lane's output regardless of entrypoint. Governance is therefore
        identical to an interactive single-skill turn.
        """
        routing = Routing(
            framework="single",
            skills=(skill_ref,),
            reason=f"scheduled run of skill {skill_ref}",
        )
        budget = TurnBudget.from_settings(self._settings)
        usage = UsageAccumulator(turn_id or "")
        fragment = _resolve_personality_fragment(self._personality_registry, personality)
        try:
            result = await self._run_single(
                user_message=user_message,
                routing=routing,
                tool_context=tool_context,
                budget=budget,
                event_bus=event_bus,
                turn_id=turn_id,
                usage=usage,
                personality_fragment=fragment,
                locale=locale,
            )
        except TurnCeilingExceededError as exc:
            logger.warning(
                "orch_skill_turn_ceiling_exceeded",
                turn_id=turn_id,
                skill=skill_ref,
                dimension=exc.dimension,
            )
            return self._finalize(self._degraded_ceiling_result(routing, exc), usage, event_bus)
        return self._finalize(result, usage, event_bus)

    # ---- none ----

    async def _run_none(  # noqa: PLR0913, PLR0917 — one field per turn-start dimension
        self,
        user_message: str,
        routing: Routing,
        tool_context: ToolContext,
        usage: UsageAccumulator,
        personality_fragment: StyleFragments,
        model_override: str | None = None,
        *,
        event_bus: EventBus | None = None,
        turn_id: str | None = None,
        web_search_enabled: bool = False,
        locale: str = "zh-TW",
        images: tuple[ImagePart, ...] = (),
        history: tuple[Message, ...] = (),
        recall_provider: RecallBlockProvider | None = None,
    ) -> OrchestratorResult:
        """Direct reply from the system prompt — no tools, no skills (unless `web_search_enabled`
        grants the two web-access tools for this turn). Runs through an `AgentLoop` with the
        resolved tool set so the mandatory `pre_output` guardrail still fires on the answer.

        `_build_loop` resolves the AGENT-role client (honoring `model_override`) and decides the
        web-search posture from that client's own capabilities — this method only forwards the
        turn's raw tool list (empty here) and toggle. `event_bus`/`turn_id` must be forwarded too
        (not just `_run_single`'s concern): `_build_loop` only wires the `web_search`/`thinking`
        event-bus hooks when it has a bus, and `none` turns can trigger native web search same
        as `single` — dropping them here would silently swallow the live `web_search.result`
        SSE frame for every `none`-framework turn."""
        loop = self._build_loop(
            tools=[],
            prompt_layers=dataclasses.replace(
                default_prompt_layers(locale),
                personality=personality_fragment.personality,
                pet_hint=personality_fragment.pet_hint,
            ),
            max_steps=1,
            role=ModelRole.AGENT,
            event_bus=event_bus,
            turn_id=turn_id,
            model_override=model_override,
            web_search_enabled=web_search_enabled,
            recall_provider=recall_provider,
        )
        turn = await loop.run_turn(
            user_message, tool_context=tool_context, images=images, history=history
        )
        self._feed_usage(usage, turn)
        # No tools run in the `none` framework, so `turn.rich_cards` is always empty here — but
        # `web_search_sources` is real: `web_search_enabled` can grant native web search even
        # with zero tool calls, so it must still surface on the result, not be silently dropped.
        return OrchestratorResult(
            final_text=turn.final_text,
            routing=routing,
            web_search_sources=turn.web_search_sources,
        )

    # ---- single ----

    async def _run_single(  # noqa: PLR0913 — threads the per-turn correlation + budget context
        self,
        *,
        user_message: str,
        routing: Routing,
        tool_context: ToolContext,
        budget: TurnBudget,
        event_bus: EventBus | None,
        turn_id: str | None,
        usage: UsageAccumulator,
        personality_fragment: StyleFragments,
        steering_source: SteeringSource | None = None,
        locale: str = "zh-TW",
        model_override: str | None = None,
        web_search_enabled: bool = False,
        images: tuple[ImagePart, ...] = (),
        history: tuple[Message, ...] = (),
        recall_provider: RecallBlockProvider | None = None,
    ) -> OrchestratorResult:
        skills = routing.bounded_skills()
        skill = self._registry.get(skills[0]) if skills else None
        tools = _tools_for(skill, self._tool_lookup) if skill else _warm_tools(self._tool_lookup)
        reservation: QuotaReservation | None = None
        if skill is not None and _has_sponsor_tool(tools):
            # The `single` framework never went through `_start_fanout` (unlike `subagent`/
            # `team`), so a skill whose allowlist carries a Sponsor-tier tool (e.g. chip-flow-
            # review's `get_chip_flows`) could spend live FinMind Sponsor budget with no
            # reservation at all — a real scope gap, since `single` handles the common one-symbol
            # turn. Reserve exactly as the fan-out paths do; a degraded reservation strips the
            # Sponsor tools from this loop's tool set rather than letting the model call one the
            # pool has no budget for.
            reservation = await self._quota_reserver.reserve(
                skills=(skill.name,), tier=tool_context.tier.value
            )
            if reservation.is_degraded(skill.name):
                tools = [tool for tool in tools if tool.data_class is not DataClass.SPONSOR]
        loop = self._build_loop(
            tools=tools,
            prompt_layers=_skill_prompt_layers(skill, personality_fragment, locale),
            max_steps=budget.max_steps_per_agent,
            role=ModelRole.AGENT,
            event_bus=event_bus,
            turn_id=turn_id,
            agent_id=_ROOT_AGENT_ID,
            steering_source=steering_source,
            model_override=model_override,
            web_search_enabled=web_search_enabled,
            locale=locale,
            recall_provider=recall_provider,
            hide_cards_for=frozenset(skill.frontmatter.hide_cards) if skill else frozenset(),
        )
        try:
            turn = await loop.run_turn(
                user_message, tool_context=tool_context, images=images, history=history
            )
        except StepBudgetExceededError:
            logger.warning("orch_single_step_budget_exceeded", turn_id=turn_id)
            return OrchestratorResult(final_text=_DEGRADED_ALL_LANES, routing=routing)
        self._feed_usage(usage, turn)
        budget.record_tool_calls(self._count_tool_calls(turn))
        # The `single` framework is the common one-shot path — a card-eligible tool call here
        # must emit its `rich_card` exactly as the fan-out lanes do, or the feature would be
        # inert for most real turns.
        self._emit_cards(
            turn.rich_cards, event_bus=event_bus, turn_id=turn_id, agent_id=_ROOT_AGENT_ID
        )
        return OrchestratorResult(
            final_text=turn.final_text,
            routing=routing,
            lanes=(_root_lane_from_turn(turn, skill=skill.name if skill else ""),),
            rich_cards=turn.rich_cards,
            web_search_sources=turn.web_search_sources,
        )

    async def _start_fanout(
        self, routing: Routing, budget: TurnBudget, tool_context: ToolContext
    ) -> tuple[tuple[str, ...], int, QuotaReservation]:
        """The setup common to both fan-out frameworks — plan which skills run (honoring the
        classifier/ceiling drop) and reserve their quota — pulled out purely so `_run_subagent`/
        `_run_team` don't repeat this identical block, which otherwise duplicates verbatim aside
        from the loop shape each uses afterward."""
        skills, dropped = self._plan_fanout(routing, budget)
        reservation = await self._quota_reserver.reserve(
            skills=skills, tier=tool_context.tier.value
        )
        return skills, dropped, reservation

    # ---- subagent (parallel) ----

    async def _run_subagent(  # noqa: PLR0913 — threads the per-turn correlation + budget context
        self,
        *,
        user_message: str,
        routing: Routing,
        tool_context: ToolContext,
        budget: TurnBudget,
        event_bus: EventBus | None,
        turn_id: str | None,
        usage: UsageAccumulator,
        personality_fragment: StyleFragments,
        locale: str = "zh-TW",
        model_override: str | None = None,
        history: tuple[Message, ...] = (),
        web_search_enabled: bool = False,
    ) -> OrchestratorResult:
        skills, dropped, reservation = await self._start_fanout(routing, budget, tool_context)

        async def run_lane(skill_name: str) -> LaneResult:
            return await self._run_one_lane(
                skill_name=skill_name,
                user_message=user_message,
                tool_context=tool_context,
                budget=budget,
                event_bus=event_bus,
                turn_id=turn_id,
                degraded=reservation.is_degraded(skill_name),
                usage=usage,
                personality_fragment=personality_fragment,
                locale=locale,
                history=history,
                web_search_enabled=web_search_enabled,
            )

        outcomes = await asyncio.gather(
            *(run_lane(name) for name in skills), return_exceptions=True
        )
        lanes = tuple(
            self._lane_from_outcome(outcome, skill=name)
            for name, outcome in zip(skills, outcomes, strict=True)
        )
        await self._refund_unspent(reservation, lanes)
        return await self._synthesize(
            routing,
            lanes,
            event_bus,
            turn_id,
            model_override,
            dropped_skill_count=dropped,
            locale=locale,
        )

    # ---- team (sequential) ----

    async def _run_team(  # noqa: PLR0913 — threads the per-turn correlation + budget context
        self,
        *,
        user_message: str,
        routing: Routing,
        tool_context: ToolContext,
        budget: TurnBudget,
        event_bus: EventBus | None,
        turn_id: str | None,
        usage: UsageAccumulator,
        personality_fragment: StyleFragments,
        locale: str = "zh-TW",
        model_override: str | None = None,
        history: tuple[Message, ...] = (),
        web_search_enabled: bool = False,
    ) -> OrchestratorResult:
        skills, dropped, reservation = await self._start_fanout(routing, budget, tool_context)
        lanes: list[LaneResult] = []
        findings: tuple[Finding, ...] = ()
        for skill_name in skills:
            prior = _render_findings(findings) if findings else None
            try:
                lane = await self._run_one_lane(
                    skill_name=skill_name,
                    user_message=user_message,
                    tool_context=tool_context,
                    budget=budget,
                    event_bus=event_bus,
                    turn_id=turn_id,
                    degraded=reservation.is_degraded(skill_name),
                    usage=usage,
                    personality_fragment=personality_fragment,
                    prior_findings=prior,
                    locale=locale,
                    history=history,
                    web_search_enabled=web_search_enabled,
                )
            except Exception as exc:
                # A teammate's failure is a missing dimension, isolated the same way a parallel
                # lane's is; the sequence continues rather than aborting the turn.
                lane = self._lane_from_outcome(exc, skill=skill_name)
            lanes.append(lane)
            if lane.succeeded:
                findings = (*findings, _finding_from_lane(lane))
        await self._refund_unspent(reservation, tuple(lanes))
        return await self._synthesize(
            routing,
            tuple(lanes),
            event_bus,
            turn_id,
            model_override,
            dropped_skill_count=dropped,
            locale=locale,
        )

    # ---- shared lane execution ----

    async def _run_one_lane(  # noqa: PLR0913 — one field per lane-spawn dimension
        self,
        *,
        skill_name: str,
        user_message: str,
        tool_context: ToolContext,
        budget: TurnBudget,
        event_bus: EventBus | None,
        turn_id: str | None,
        degraded: bool,
        usage: UsageAccumulator,
        personality_fragment: StyleFragments,
        prior_findings: str | None = None,
        locale: str = "zh-TW",
        history: tuple[Message, ...] = (),
        web_search_enabled: bool = False,
    ) -> LaneResult:
        """Run one lane's Step loop, with one corrective retry on an empty/degraded result then
        drop-with-flag. Sub-agents are read-only + independent, so the retry is
        idempotent. A quota-degraded lane is flagged (cached/unavailable-tier posture) before
        it even spawns rather than fanning out on unavailable Sponsor budget."""
        agent_id = f"{skill_name}:{uuid.uuid4().hex[:8]}"
        skill = self._registry.get(skill_name)
        tools = _tools_for(skill, self._tool_lookup)
        activity = skill_activity_label(skill_name, locale=locale)
        if event_bus is not None:
            event_bus.publish(
                EventKind.AGENT_SPAWNED,
                turn_id=turn_id,
                agent_id=agent_id,
                parent_agent_id=_ROOT_AGENT_ID,
                data={
                    "agent_id": agent_id,
                    "parent_agent_id": _ROOT_AGENT_ID,
                    "role": ModelRole.SUBAGENT.value,
                    "skill": skill_name,
                    "activity": activity,
                },
            )

        if degraded:
            if event_bus is not None:
                event_bus.publish(
                    EventKind.AGENT_PROGRESS,
                    turn_id=turn_id,
                    agent_id=agent_id,
                    data={"agent_id": agent_id, "status": "degraded", "phase": "quota"},
                )
            return LaneResult(
                agent_id=agent_id,
                skill=skill_name,
                status="unavailable_tier",
                completeness="empty",
                confidence=0.0,
                reason="FinMind quota unavailable — lane degraded to cached/unavailable-tier "
                "before spawning",
            )

        lane_message = (
            f"{prior_findings}\n\n---\n\nUser request:\n{user_message}"
            if prior_findings
            else user_message
        )
        lane = await self._run_lane_attempt(
            skill=skill,
            tools=tools,
            lane_message=lane_message,
            tool_context=tool_context,
            budget=budget,
            event_bus=event_bus,
            turn_id=turn_id,
            agent_id=agent_id,
            usage=usage,
            personality_fragment=personality_fragment,
            locale=locale,
            history=history,
            web_search_enabled=web_search_enabled,
        )
        if lane.completeness == "empty":
            lane = await self._run_lane_attempt(
                skill=skill,
                tools=tools,
                lane_message=lane_message,
                tool_context=tool_context,
                budget=budget,
                event_bus=event_bus,
                turn_id=turn_id,
                agent_id=agent_id,
                usage=usage,
                personality_fragment=personality_fragment,
                locale=locale,
                history=history,
                web_search_enabled=web_search_enabled,
            )
            if lane.completeness == "empty":
                lane = lane.model_copy(
                    update={
                        "status": "dropped",
                        "reason": "empty after one corrective retry — dropped with flag",
                    }
                )
        self._emit_cards(lane.rich_cards, event_bus=event_bus, turn_id=turn_id, agent_id=agent_id)
        if event_bus is not None:
            event_bus.publish(
                EventKind.AGENT_DONE,
                turn_id=turn_id,
                agent_id=agent_id,
                data={"agent_id": agent_id, "status": lane.status},
            )
        return lane

    async def _run_lane_attempt(  # noqa: PLR0913 — one field per lane-attempt dimension
        self,
        *,
        skill: Skill,
        tools: list[Tool],
        lane_message: str,
        tool_context: ToolContext,
        budget: TurnBudget,
        event_bus: EventBus | None,
        turn_id: str | None,
        agent_id: str,
        usage: UsageAccumulator,
        personality_fragment: StyleFragments,
        locale: str = "zh-TW",
        history: tuple[Message, ...] = (),
        web_search_enabled: bool = False,
    ) -> LaneResult:
        loop = self._build_loop(
            tools=tools,
            prompt_layers=_skill_prompt_layers(skill, personality_fragment, locale),
            max_steps=budget.max_steps_per_agent,
            role=ModelRole.SUBAGENT,
            event_bus=event_bus,
            turn_id=turn_id,
            agent_id=agent_id,
            web_search_enabled=web_search_enabled,
            locale=locale,
            hide_cards_for=frozenset(skill.frontmatter.hide_cards),
        )
        try:
            turn = await loop.run_turn(lane_message, tool_context=tool_context, history=history)
        except StepBudgetExceededError:
            return LaneResult(
                agent_id=agent_id,
                skill=skill.name,
                status="degraded",
                completeness="empty",
                confidence=0.0,
                reason="per-agent ReAct-iteration cap reached without a final answer",
            )
        self._feed_usage(usage, turn)
        budget.record_tool_calls(self._count_tool_calls(turn))
        return _lane_from_turn(turn, agent_id=agent_id, skill=skill.name)

    def _lane_from_outcome(self, outcome: LaneResult | BaseException, *, skill: str) -> LaneResult:
        """Normalize a `gather(return_exceptions=True)` outcome: a `LaneResult` passes through;
        an exception becomes a lane-scoped `error` (isolation — one lane's crash never kills
        siblings or the turn)."""
        if isinstance(outcome, LaneResult):
            return outcome
        logger.warning("orch_lane_isolated_exception", skill=skill, error=str(outcome))
        # The raw exception (`str(outcome)`) is logged above for operators only — it can carry
        # an internal validation-error message naming implementation details (a tool's Pydantic
        # field, a source adapter's capability check) that must never reach the synthesis prompt
        # or the user; `reason` here is what `Synthesizer` renders into that prompt verbatim.
        return LaneResult(
            agent_id=f"{skill}:errored",
            skill=skill,
            status="error",
            completeness="empty",
            confidence=0.0,
            reason="this lane could not complete for this query",
        )

    async def _synthesize(  # noqa: PLR0913 — one field per synthesis-input dimension
        self,
        routing: Routing,
        lanes: tuple[LaneResult, ...],
        event_bus: EventBus | None,
        turn_id: str | None,
        model_override: str | None = None,
        *,
        dropped_skill_count: int = 0,
        locale: str = "zh-TW",
    ) -> OrchestratorResult:
        if event_bus is not None:
            event_bus.publish(
                EventKind.SYNTHESIS_START,
                turn_id=turn_id,
                agent_id=_ROOT_AGENT_ID,
                data={"from_agents": [lane.agent_id for lane in lanes if lane.succeeded]},
            )
        try:
            final_text, provenance = await self._synthesizer.synthesize(
                lanes, model_override=model_override, locale=locale
            )
        except NoSuccessfulLanesError:
            logger.warning("orch_all_lanes_failed", turn_id=turn_id)
            provenance = SynthesisProvenance(
                contributing_skills=(),
                missing_skills=tuple(lane.skill for lane in lanes),
                degraded=True,
                dropped_skill_count=dropped_skill_count,
            )
            return OrchestratorResult(
                final_text=_append_dropped_notice(_DEGRADED_ALL_LANES, dropped_skill_count),
                routing=routing,
                lanes=lanes,
                provenance=provenance,
                rich_cards=_lane_cards(lanes),
                web_search_sources=_lane_web_search_sources(lanes),
            )
        provenance = provenance.model_copy(update={"dropped_skill_count": dropped_skill_count})
        logger.info(
            "synthesis_provenance",
            turn_id=turn_id,
            contributing=list(provenance.contributing_skills),
            missing=list(provenance.missing_skills),
            degraded=provenance.degraded,
            dropped_skill_count=dropped_skill_count,
        )
        return OrchestratorResult(
            final_text=_append_dropped_notice(final_text, dropped_skill_count),
            routing=routing,
            lanes=lanes,
            provenance=provenance,
            rich_cards=_lane_cards(lanes),
            web_search_sources=_lane_web_search_sources(lanes),
        )

    def _emit_cards(
        self,
        cards: tuple[RichCard, ...],
        *,
        event_bus: EventBus | None,
        turn_id: str | None,
        agent_id: str,
    ) -> None:
        """Publish one `rich_card` event per governed card onto the live bus, attributed to the
        emitting agent lane. The card payload is already governance-scanned and caveated by the
        Step loop that built it — this only projects it onto the stream, matching the existing
        `tool.result`/`agent.done` publish idiom."""
        if event_bus is None:
            return
        for card in cards:
            event_bus.publish(
                EventKind.RICH_CARD,
                turn_id=turn_id,
                agent_id=agent_id,
                data={"card_type": card.card_type, "data": card.data},
            )

    def _plan_fanout(self, routing: Routing, budget: TurnBudget) -> tuple[tuple[str, ...], int]:
        """Returns the clamped skill tuple and how many analytical angles were dropped along the
        way — combining the classifier's own pre-truncation count (`routing.requested_skill_count`,
        which may exceed `routing.skills` if the `_MAX_SKILLS` backstop already trimmed it) with
        whatever `TurnBudget.clamp_subagents` additionally clamps. Callers surface this in
        synthesis rather than silently absorbing it."""
        skills = routing.bounded_skills()
        clamped = budget.clamp_subagents(len(skills))
        final_skills = skills[:clamped]
        requested = max(routing.requested_skill_count, len(routing.skills))
        dropped = max(0, requested - len(final_skills))
        return final_skills, dropped

    def _degraded_ceiling_result(
        self, routing: Routing, exc: TurnCeilingExceededError
    ) -> OrchestratorResult:
        text = (
            f"This request hit a per-turn {exc.dimension} ceiling, so the analysis was stopped "
            f"early and returned incomplete. This is informational only and not a buy/sell/hold "
            f"recommendation."
        )
        return OrchestratorResult(final_text=text, routing=routing)

    def _count_tool_calls(self, turn: TurnResult) -> int:
        return sum(len(step.tool_messages) for step in turn.steps)

    def _feed_usage(self, usage: UsageAccumulator, turn: TurnResult) -> None:
        """Fold one completed lane/turn's token + tool consumption into the shared per-turn
        accumulator, so the durable post-turn sink and the live `usage` event both reflect the
        whole fan-out, not just the root lane."""
        for step in turn.steps:
            step_usage = step.response.usage
            usage.add_llm_call(
                input_tokens=step_usage.input_tokens,
                output_tokens=step_usage.output_tokens,
                cost_micros=step_usage.cost_micros,
                provider=step_usage.provider,
                model=step_usage.model,
                cached_tokens=step_usage.cached_tokens,
                cache_write_tokens=step_usage.cache_write_tokens,
                reasoning_tokens=step_usage.reasoning_tokens,
            )
            for _ in step.tool_messages:
                usage.add_tool_call()

    def _finalize(
        self,
        result: OrchestratorResult,
        usage: UsageAccumulator,
        event_bus: EventBus | None,
    ) -> OrchestratorResult:
        """Attach the per-turn usage snapshot to the result and emit the live `usage` event. The
        snapshot is what the request/scheduler layer writes through to the durable usage sink at
        post-turn — the orchestrator itself never touches the DB."""
        if event_bus is not None:
            usage.publish(event_bus, agent_id=_ROOT_AGENT_ID)
        return result.model_copy(update={"usage": usage.snapshot()})

    async def _refund_unspent(
        self, reservation: QuotaReservation, lanes: tuple[LaneResult, ...]
    ) -> None:
        """Credit back the FinMind reservation for granted lanes that did not actually spend their
        Sponsor budget (failed/dropped/empty before any real fetch), so a reservation never
        permanently burns pool budget the fan-out didn't consume. No-op unless the reserver exposes
        a `refund` (the permissive default doesn't)."""
        refund = getattr(self._quota_reserver, "refund", None)
        if refund is None or not reservation.granted:
            return
        granted = set(reservation.granted)
        unspent = sum(1 for lane in lanes if lane.skill in granted and not lane.succeeded)
        if unspent > 0:
            await refund(lanes=unspent)

    def _build_loop(  # noqa: PLR0913 — mirrors AgentLoop's own knob set, per-lane
        self,
        *,
        tools: list[Tool],
        prompt_layers: PromptLayers,
        max_steps: int,
        role: ModelRole,
        event_bus: EventBus | None = None,
        turn_id: str | None = None,
        agent_id: str = _ROOT_AGENT_ID,
        steering_source: SteeringSource | None = None,
        model_override: str | None = None,
        web_search_enabled: bool = False,
        locale: str = "zh-TW",
        recall_provider: RecallBlockProvider | None = None,
        hide_cards_for: frozenset[str] = frozenset(),
    ) -> AgentLoop:
        pre_tool_hooks: list[PreToolHook] | None = None
        post_tool_hooks: list[PostToolHook] | None = None
        thinking_hooks: list[ThinkingHook] | None = None
        answer_delta_hooks: list[AnswerDeltaHook] | None = None
        web_search_hooks: list[WebSearchHook] | None = None
        if event_bus is not None:
            from events.hooks import (  # noqa: PLC0415 — same-layer import
                EventBusAnswerDeltaHook,
                EventBusPostToolHook,
                EventBusPreToolHook,
                EventBusThinkingHook,
                EventBusWebSearchHook,
            )

            pre_tool_hooks = [
                EventBusPreToolHook(event_bus, turn_id=turn_id or "", agent_id=agent_id)
            ]
            post_tool_hooks = [
                EventBusPostToolHook(event_bus, turn_id=turn_id or "", agent_id=agent_id)
            ]
            thinking_hooks = [
                EventBusThinkingHook(event_bus, turn_id=turn_id or "", agent_id=agent_id)
            ]
            web_search_hooks = [
                EventBusWebSearchHook(event_bus, turn_id=turn_id or "", agent_id=agent_id)
            ]
            # Only the root AGENT-role loop's own text is the answer a user reads directly
            # (`none`/`single`, and `_synthesize`'s own dedicated AgentLoop-free call — see
            # below) — a SUBAGENT-role fan-out lane's text is an intermediate `Finding`/lane
            # result the synthesis pass consumes, never streamed to the user as-is, so wiring
            # this hook there would surface half-formed intermediate prose as if it were the
            # final answer. Mirrors the `recall_provider` root-only carve-out already applied
            # a few lines below.
            if role is ModelRole.AGENT:
                answer_delta_hooks = [
                    EventBusAnswerDeltaHook(event_bus, turn_id=turn_id or "", agent_id=agent_id)
                ]
        # Long-term-memory recall injects a `pre_llm` SYSTEM block; only the `none`/`single`
        # root-agent paths pass a `recall_provider` (fan-out lanes never do — see `run`'s
        # docstring), so this is `None` for every SUBAGENT-role loop, exactly like
        # `web_search_enabled` below.
        pre_llm_hooks: list[PreLLMHook] | None = None
        if recall_provider is not None:
            from events.hooks import MemoryRecallPreLLMHook  # noqa: PLC0415 — same-layer import

            pre_llm_hooks = [MemoryRecallPreLLMHook(recall_provider)]
        client = self._resolve_client(role, model_override=model_override)
        # `web_search_enabled` is the user's per-turn opt-in and reaches every framework path,
        # fan-out lanes included; the resolved client decides native vs Brave-tool grounding.
        tools, native_web_search = _maybe_add_web_search(
            tools, self._tool_lookup, client, web_search_enabled=web_search_enabled
        )
        return AgentLoop(
            client,
            tools=tools,
            prompt_layers=prompt_layers,
            max_steps=max_steps,
            settings=self._settings,
            pre_tool_hooks=pre_tool_hooks,
            post_tool_hooks=post_tool_hooks,
            thinking_hooks=thinking_hooks,
            answer_delta_hooks=answer_delta_hooks,
            web_search_hooks=web_search_hooks,
            pre_llm_hooks=pre_llm_hooks,
            steering_source=steering_source,
            native_web_search=native_web_search,
            hide_cards_for=hide_cards_for,
        )

    def _resolve_client(self, role: ModelRole, *, model_override: str | None = None) -> LLMClient:
        """Resolve the Step-loop client for a role: single/none reply agents use the strong
        AGENT role, fan-out lanes use the cheap/fast SUBAGENT role. Injected clients (tests)
        take precedence; otherwise route via `llm/registry.py` — never a hardcoded model.
        `model_override` is only consulted for the non-SUBAGENT (AGENT) path — fan-out lanes
        never take a thread's model override, regardless of what the caller passes here."""
        if role is ModelRole.SUBAGENT:
            if self._subagent_client is not None:
                return self._subagent_client
            try:
                return resolve_role(ModelRole.SUBAGENT, self._settings)
            except ProviderNotConfiguredError:
                return resolve_role(ModelRole.AGENT, self._settings)
        if self._agent_client is not None:
            return self._agent_client
        return self._resolve_agent_client(model_override)

    def agent_client(self, model_override: str | None = None) -> LLMClient:
        """The AGENT-role client (an injected one in tests, else role-resolved) - for callers
        outside the orchestrator that need the model the reply agent would use, e.g. deep
        research."""
        return self._resolve_client(ModelRole.AGENT, model_override=model_override)

    def _resolve_agent_client(self, model_override: str | None) -> LLMClient:
        """The AGENT-role client, honoring a per-thread `model_override` (a `"provider/model"`
        string) when present — see `llm.registry.resolve_role_with_override` for the shared
        degrade-on-failure behavior (also used by `Synthesizer` for the SYNTHESIS role)."""
        return resolve_role_with_override(ModelRole.AGENT, model_override, self._settings)


__all__ = ["Orchestrator", "PersonalityContext"]
