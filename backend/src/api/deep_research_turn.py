"""Run one deep-research turn inside the normal chat turn stream (Pro).

The turn driver calls this instead of the orchestrator when the request asked for deep research.
It wires the real collaborators into `research.DeepResearch`, turns its progress into the same
`agent.spawned` / `agent.done` lane frames the chat UI already renders, and returns an ordinary
`OrchestratorResult` so persistence, usage recording and the final `message` frame are unchanged.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import structlog

from api.dependencies import AppState
from dataaccess.web_search import WebSearchGateway
from events.bus import EventBus
from events.usage import UsageAccumulator
from llm.base import WebSearchSource
from orchestrator.models import OrchestratorResult, Routing
from research import DeepResearch, Progress, ResearchBudget, ResearchFailedError
from research.adapters import GatewayWeb, LLMResearchModel, governance_guard
from research.render import Locale, failure_text, render_markdown
from tools.base import ToolContext

logger = structlog.get_logger(__name__)

# Three rounds: enough to follow up on a gap, not enough to run away.
BUDGET = ResearchBudget(max_rounds=3, max_searches=8, max_pages=12, max_seconds=120.0)

_STAGE_LABELS: dict[Locale, dict[str, str]] = {
    "zh-TW": {
        "planning": "規劃研究方向",
        "searching": "搜尋：{detail}",
        "reading": "閱讀 {detail} 個網頁",
        "reviewing": "檢視資料是否足夠",
        "writing": "撰寫研究報告",
    },
    "en": {
        "planning": "Planning the research",
        "searching": "Searching: {detail}",
        "reading": "Reading {detail} pages",
        "reviewing": "Checking whether this is enough",
        "writing": "Writing the report",
    },
}

_ROOT_AGENT_ID = "root"


def build_gateway() -> WebSearchGateway:
    """The web gateway; a seam so tests can substitute a fake without touching the network."""
    return WebSearchGateway()


@dataclass(frozen=True)
class DeepResearchOutcome:
    result: OrchestratorResult
    counts_toward_limit: bool  # False -> the caller refunds the claimed report


class _Lanes:
    """Shows the research stages as a sequence of lanes, one open at a time."""

    def __init__(self, bus: EventBus, turn_id: str, locale: Locale) -> None:
        self._bus = bus
        self._turn_id = turn_id
        self._labels = _STAGE_LABELS[locale]
        self._open: str | None = None

    async def __call__(self, progress: Progress) -> None:
        self.close()
        agent_id = f"deep_research:{uuid.uuid4().hex[:8]}"
        activity = self._labels[progress.stage].format(detail=progress.detail)
        self._bus.publish(
            "agent.spawned",
            turn_id=self._turn_id,
            agent_id=agent_id,
            parent_agent_id=_ROOT_AGENT_ID,
            data={
                "agent_id": agent_id,
                "parent_agent_id": _ROOT_AGENT_ID,
                "role": "subagent",
                "skill": "deep_research",
                "activity": activity,
            },
        )
        self._open = agent_id

    def close(self) -> None:
        if self._open is not None:
            self._bus.publish(
                "agent.done",
                turn_id=self._turn_id,
                agent_id=self._open,
                data={"agent_id": self._open, "status": "ok"},
            )
            self._open = None


async def run_deep_research_turn(
    *,
    state: AppState,
    ctx: ToolContext,
    event_bus: EventBus,
    turn_id: str,
    question: str,
    locale: str,
    model_override: str | None,
) -> DeepResearchOutcome:
    ui_locale: Locale = "en" if locale.lower().startswith("en") else "zh-TW"
    usage = UsageAccumulator(turn_id)
    client = state.orchestrator.agent_client(model_override)
    research = DeepResearch(
        model=LLMResearchModel(
            client,
            on_usage=lambda call: usage.add_llm_call(
                input_tokens=call.input_tokens,
                output_tokens=call.output_tokens,
                cost_micros=call.cost_micros,
                provider=call.provider,
                model=call.model,
                cached_tokens=call.cached_tokens,
                cache_write_tokens=call.cache_write_tokens,
                reasoning_tokens=call.reasoning_tokens,
            ),
        ),
        web=GatewayWeb(build_gateway(), tls_policy=ctx.tls_policy),
        guard=governance_guard,
        budget=BUDGET,
    )
    lanes = _Lanes(event_bus, turn_id, ui_locale)
    routing = Routing(framework="none", reason="deep_research")
    try:
        report = await research.run(question, progress=lanes)
    except ResearchFailedError as exc:
        logger.warning("deep_research_failed", turn_id=turn_id, error=str(exc))
        return DeepResearchOutcome(
            OrchestratorResult(
                final_text=failure_text(ui_locale), routing=routing, usage=usage.snapshot()
            ),
            counts_toward_limit=False,
        )
    finally:
        lanes.close()

    usage.publish(event_bus)
    sources = tuple(WebSearchSource(url=s.url, title=s.title) for s in report.sources)
    if sources:
        event_bus.publish(
            "web_search.result",
            turn_id=turn_id,
            agent_id=_ROOT_AGENT_ID,
            data={
                "agent_id": _ROOT_AGENT_ID,
                "sources": [{"url": s.url, "title": s.title} for s in sources],
            },
        )
    return DeepResearchOutcome(
        OrchestratorResult(
            final_text=render_markdown(report, ui_locale),
            routing=routing,
            usage=usage.snapshot(),
            web_search_sources=sources,
        ),
        counts_toward_limit=True,  # searching/reading already cost; only our own failures refund
    )


__all__ = ["BUDGET", "DeepResearchOutcome", "build_gateway", "run_deep_research_turn"]
