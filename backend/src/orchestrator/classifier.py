"""The intent classifier: a cheap, fast LLM call that reads the user message + the skill
menu and returns a `Routing` (framework + 0-4 skills + reason).

A misrouted turn is the highest-leverage error in this system — it silently sends the wrong
tools/skills to the loop — so the classifier is defence-in-depth: the LLM call is wrapped in a
try/except that, on *any* failure (transport error, unparseable JSON, schema-invalid verdict),
falls back to a deterministic keyword match over each skill's `triggers` and defaults the
framework to `single` — the safe, cheap floor. Routing never hard-fails. An unambiguous
single-trigger hit does not pre-pin that skill as a fast-path when the LLM is available — the
LLM is preferred when it succeeds; the keyword path is strictly the fallback, used only when
the classifier call itself fails.
"""

from __future__ import annotations

import json
from typing import cast

import structlog

from events.bus import EventBus
from llm.base import LLMClient, Message, MessageRole
from llm.registry import ModelRole, ProviderNotConfiguredError, resolve_role
from orchestrator.models import Framework, Routing
from skills.registry import SkillRegistry

logger = structlog.get_logger(__name__)

_VALID_FRAMEWORKS: frozenset[str] = frozenset({"none", "single", "subagent", "team"})
_MAX_SKILLS = 4

_ROUTER_SYSTEM_PROMPT = (
    "You are the strategy router for a governed, non-trading Taiwan-stock/ETF analysis agent. "
    "Read the user message and the available skills, then choose how much machinery the query "
    "needs. Respond with ONLY a JSON object, no prose, of the exact shape:\n"
    '{"framework": "none|single|subagent|team", "skills": ["skill-name", ...], '
    '"reason": "short rationale"}\n\n'
    "Framework guidance:\n"
    "- none: greeting, off-topic, or a capability question — reply from the system prompt, no "
    "skills, skills MUST be [].\n"
    "- single: one-dimension lookup or action — exactly one skill (or none if no skill fits).\n"
    "- subagent: multi-angle analysis across INDEPENDENT dimensions — 2 to 4 skills, run in "
    "parallel.\n"
    "- team: sequential deep research where later findings depend on earlier ones — 2 to 4 "
    "skills, run in order.\n"
    "Pick at most 4 skills, only from the provided list, by their exact name. Prefer 'single' "
    "for ambiguous intent rather than guessing a fan-out."
)


def _render_skill_menu(registry: SkillRegistry) -> str:
    lines = [f"- {entry.name}: {entry.description}" for entry in registry.menu()]
    if not lines:
        return "(no skills available)"
    return "\n".join(lines)


def _keyword_fallback(message: str, registry: SkillRegistry) -> Routing:
    """Deterministic fallback: keyword-match the message against each skill's `triggers`,
    default the framework to `single`. A single matched skill is pre-pinned; multiple matches
    still route `single` (the safe floor) with the best-matching skill first — the spec
    reserves `subagent`/`team` for the LLM's judgment, never a keyword heuristic."""
    lowered = message.lower()
    scored: list[tuple[int, str]] = []
    for skill in registry.all_skills():
        hits = sum(1 for trigger in skill.frontmatter.triggers if trigger.lower() in lowered)
        if hits:
            scored.append((hits, skill.name))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    matched = tuple(name for _, name in scored[:1])
    reason = (
        f"classifier fallback: keyword-matched {matched[0]!r}"
        if matched
        else "classifier fallback: no keyword match — bare single lookup"
    )
    return Routing(framework="single", skills=matched, reason=reason)


def _parse_routing(text: str, registry: SkillRegistry) -> Routing:
    """Parse the LLM's JSON verdict into a validated `Routing`, clamping to known skills and the
    4-skill ceiling. Raises `ValueError` on anything unparseable/invalid so the caller falls
    back to the keyword path."""
    raw = text.strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in classifier response")
    loaded: object = json.loads(raw[start : end + 1])
    if not isinstance(loaded, dict):
        raise ValueError("classifier response is not a JSON object")
    parsed = cast("dict[str, object]", loaded)

    framework = parsed.get("framework")
    if not isinstance(framework, str) or framework not in _VALID_FRAMEWORKS:
        raise ValueError(f"invalid framework: {framework!r}")

    raw_skills = parsed.get("skills", [])
    if not isinstance(raw_skills, list):
        raise ValueError("skills is not a list")
    skill_items = cast("list[object]", raw_skills)
    known = {skill.name for skill in registry.all_skills()}
    skills: tuple[str, ...] = tuple(
        name for name in skill_items if isinstance(name, str) and name in known
    )[:_MAX_SKILLS]

    framework_literal: Framework = cast("Framework", framework)
    if framework_literal == "none":
        skills = ()

    reason = parsed.get("reason")
    reason_text = reason if isinstance(reason, str) and reason.strip() else "classifier verdict"
    return Routing(framework=framework_literal, skills=skills, reason=reason_text)


class IntentClassifier:
    """Classifies a user message into a `Routing`. Uses the cheap CLASSIFIER model role; falls
    back to the deterministic keyword path on any failure. Emits the routing decision as a
    journaled `route.decided` event when an event bus is supplied."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        client: LLMClient | None = None,
    ) -> None:
        self._registry = registry
        self._client = client

    def _resolve_client(self) -> LLMClient | None:
        if self._client is not None:
            return self._client
        try:
            return resolve_role(ModelRole.CLASSIFIER)
        except ProviderNotConfiguredError:
            logger.warning("classifier_provider_unconfigured_falling_back_to_keywords")
            return None

    async def classify(
        self,
        message: str,
        *,
        event_bus: EventBus | None = None,
        turn_id: str | None = None,
    ) -> Routing:
        routing = await self._classify_inner(message)
        if event_bus is not None:
            event_bus.publish(
                "route.decided",
                turn_id=turn_id,
                data={
                    "framework": routing.framework,
                    "skills": list(routing.skills),
                    "reason": routing.reason,
                },
            )
        logger.info(
            "route_decided",
            turn_id=turn_id,
            framework=routing.framework,
            skills=list(routing.skills),
            reason=routing.reason,
        )
        return routing

    async def _classify_inner(self, message: str) -> Routing:
        client = self._resolve_client()
        if client is None:
            return _keyword_fallback(message, self._registry)
        messages = [
            Message(role=MessageRole.SYSTEM, content=_ROUTER_SYSTEM_PROMPT),
            Message(
                role=MessageRole.USER,
                content=(
                    f"Available skills:\n{_render_skill_menu(self._registry)}\n\n"
                    f"User message:\n{message}"
                ),
            ),
        ]
        try:
            response = await client.chat(messages)
            return _parse_routing(response.text or "", self._registry)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning("classifier_parse_failed_falling_back", error=str(exc))
            return _keyword_fallback(message, self._registry)
        except Exception as exc:
            # Routing must never hard-fail: any transport/provider error degrades to the
            # deterministic keyword floor rather than propagating.
            logger.warning("classifier_call_failed_falling_back", error=str(exc))
            return _keyword_fallback(message, self._registry)


__all__ = ["IntentClassifier"]
