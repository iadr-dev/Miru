"""Run one deep-research turn inside the normal chat turn stream (Pro).

The turn driver calls this instead of the orchestrator when the request asked for deep research.
It wires the real collaborators into `research.DeepResearch` - the model, the web gateway, and the
tool registry for Miru's own market data - passes in the conversation so far and any attached
images, turns the research progress into the same `agent.spawned` / `agent.done` lane frames the
chat UI already renders (one lane per sub-question, since they run side by side), and returns an
ordinary `OrchestratorResult` so persistence, usage recording and the final `message` frame are
unchanged.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import structlog

from api.dependencies import AppState
from dataaccess.web_search import WebSearchGateway
from events.bus import EventBus
from events.kinds import EventKind
from events.usage import UsageAccumulator
from llm.base import ImagePart, Message, WebSearchSource
from orchestrator.models import OrchestratorResult, Routing
from research import DeepResearch, Progress, ResearchBudget, ResearchFailedError
from research.adapters import GatewayWeb, LLMResearchModel, ToolData, governance_guard
from research.render import Locale, failure_text, render_markdown
from tools.base import ToolContext

logger = structlog.get_logger(__name__)

# Up to five sub-questions at once, each with three search -> read -> review rounds, sharing these
# totals; three minutes of wall clock. Big enough to be worth waiting for, bounded so a run cannot
# run away.
BUDGET = ResearchBudget(
    max_rounds=3,
    max_searches=24,
    max_pages=30,
    max_data_calls=12,
    max_seconds=180.0,
)

_STAGE_LABELS: dict[Locale, dict[str, str]] = {
    "zh-TW": {
        "understanding": "理解問題與對話脈絡",
        "planning": "規劃研究方向",
        "planned": "拆成 {detail} 個子題，同時研究",
        "searching": "搜尋：{detail}",
        "reading": "閱讀 {detail} 個網頁",
        "data": "查詢市場資料：{detail}",
        "reviewing": "檢視資料是否足夠",
        "writing": "撰寫研究報告",
    },
    "en": {
        "understanding": "Understanding the question and the conversation",
        "planning": "Planning the research",
        "planned": "Split into {detail} sub-questions, researching them side by side",
        "searching": "Searching: {detail}",
        "reading": "Reading {detail} pages",
        "data": "Looking up market data: {detail}",
        "reviewing": "Checking whether this is enough",
        "writing": "Writing the report",
    },
}

_ROOT_AGENT_ID = "root"
_RUN_LANE = -1  # the whole-run stages (understanding, planning, writing)


def build_gateway() -> WebSearchGateway:
    """The web gateway; a seam so tests can substitute a fake without touching the network."""
    return WebSearchGateway()


class _Lanes:
    """Shows the research stages as `agent.spawned` lanes: one open at a time per sub-question
    (they run side by side), plus one for the whole-run stages."""

    def __init__(self, bus: EventBus, turn_id: str, locale: Locale) -> None:
        self._bus = bus
        self._turn_id = turn_id
        self._labels = _STAGE_LABELS[locale]
        self._open: dict[int, str] = {}

    async def __call__(self, progress: Progress) -> None:
        if progress.lane == _RUN_LANE:
            self.close()  # a whole-run stage starts only once the previous stage is over
        else:
            self._close_lane(progress.lane)
        agent_id = f"deep_research:{uuid.uuid4().hex[:8]}"
        activity = self._labels[progress.stage].format(detail=progress.detail)
        if progress.lane != _RUN_LANE:
            activity = f"[{progress.lane + 1}] {activity}"
        self._bus.publish(
            EventKind.AGENT_SPAWNED,
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
        self._open[progress.lane] = agent_id

    def _close_lane(self, lane: int) -> None:
        agent_id = self._open.pop(lane, None)
        if agent_id is not None:
            self._bus.publish(
                EventKind.AGENT_DONE,
                turn_id=self._turn_id,
                agent_id=agent_id,
                data={"agent_id": agent_id, "status": "ok"},
            )

    def close(self) -> None:
        for lane in list(self._open):
            self._close_lane(lane)


async def run_deep_research_turn(
    *,
    state: AppState,
    ctx: ToolContext,
    event_bus: EventBus,
    turn_id: str,
    question: str,
    locale: str,
    model_override: str | None,
    history: Sequence[Message] = (),
    images: Sequence[ImagePart] = (),
) -> OrchestratorResult:
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
        data=ToolData(tools=state.tool_registry.get_tool, ctx=ctx, locale=ui_locale),
        guard=governance_guard,
        budget=BUDGET,
    )
    lanes = _Lanes(event_bus, turn_id, ui_locale)
    routing = Routing(framework="none", reason="deep_research")
    try:
        report = await research.run(question, history=history, images=images, progress=lanes)
    except ResearchFailedError as exc:
        logger.warning("deep_research_failed", turn_id=turn_id, error=str(exc))
        return OrchestratorResult(
            final_text=failure_text(ui_locale), routing=routing, usage=usage.snapshot()
        )
    finally:
        lanes.close()

    usage.publish(event_bus)
    sources = tuple(
        WebSearchSource(url=s.url, title=s.title)
        for s in report.sources
        if s.kind == "web" and s.url is not None
    )
    if sources:
        event_bus.publish(
            EventKind.WEB_SEARCH_RESULT,
            turn_id=turn_id,
            agent_id=_ROOT_AGENT_ID,
            data={
                "agent_id": _ROOT_AGENT_ID,
                "sources": [{"url": s.url, "title": s.title} for s in sources],
            },
        )
    return OrchestratorResult(
        final_text=render_markdown(report, ui_locale),
        routing=routing,
        usage=usage.snapshot(),
        web_search_sources=sources,
    )


__all__ = ["BUDGET", "build_gateway", "run_deep_research_turn"]
