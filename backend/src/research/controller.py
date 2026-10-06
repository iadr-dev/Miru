"""The deep-research loop: plan -> (search -> read -> review)* -> write.

**Code runs the loop; the model is only asked to plan, to judge "is this enough?", and to write.**
Rounds, searches, pages, wall-clock time and the stop rule are all decided here, so a model that
never says "done" cannot run up cost, and every exit - sufficient, ceiling hit, out of time - still
ends in a report built from whatever was actually read.

Fetched text is untrusted (see `research.untrusted`), citations are enforced structurally (see
`research.report`), and the finished text goes through the governance output guard.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import structlog

from research.ledger import SourceLedger
from research.models import (
    DraftReport,
    FinalReport,
    GapReview,
    Plan,
    ResearchBudget,
    ResearchFailedError,
    StopReason,
)
from research.ports import FetchedPage, ResearchModel, WebPort
from research.report import TextGuard, build_report
from research.untrusted import UNTRUSTED_NOTICE, looks_like_injection, render_sources

logger = structlog.get_logger(__name__)

Stage = Literal["planning", "searching", "reading", "reviewing", "writing"]

# The final write always gets at least this long, even if the research itself ran out of time:
# ending with sources read but no report would waste the whole run.
_MIN_WRITE_SECONDS = 25.0
_REVIEW_EXCERPT_CHARS = 700
_HITS_KEPT_PER_PAGE_READ = 3  # search hits kept per page we may read, so later rounds have choice

_LANGUAGE = "Write in the same language as the question (Traditional Chinese if it is Chinese)."

PLAN_SYSTEM = (
    "You plan web research for an assistant that analyses Taiwan and US stocks and ETFs. Break the "
    "question into 2-5 sub-questions and give 2-5 plain web-search queries likely to surface "
    "authoritative, recent, factual sources (company filings, exchange announcements, reputable "
    "news, official statistics). Never phrase queries around buying or selling. " + _LANGUAGE
)

REVIEW_SYSTEM = (
    "You review research progress. Given the question and the source excerpts read so far, decide "
    "whether they are enough to answer it with cited facts. If not, give up to 3 NEW search "
    "queries aimed at exactly what is missing. " + UNTRUSTED_NOTICE
)

WRITE_SYSTEM = (
    "Write a research report answering the question using ONLY the sources provided. Every claim "
    "must list, in source_ids, the ids of the sources that state it; omit any claim you cannot tie "
    "to a source. Give a title, 2-4 short summary claims, then sections with headings. State dates "
    "('as of ...') and mention disagreement between sources. Put what the sources do not settle in "
    "open_questions. This is information, not advice: never recommend buying, selling or holding, "
    "and never give price targets or position sizes. " + _LANGUAGE + " " + UNTRUSTED_NOTICE
)


@dataclass(frozen=True)
class Progress:
    stage: Stage
    detail: str = ""
    round: int = 0


ProgressFn = Callable[[Progress], Awaitable[None]]


async def _ignore(_: Progress) -> None:
    return None


def _unique(queries: list[str], used: set[str]) -> list[str]:
    fresh: list[str] = []
    for query in queries:
        key = " ".join(query.lower().split())
        if key and key not in used:
            used.add(key)
            fresh.append(query.strip())
    return fresh


@dataclass
class _Run:
    """The state of one research run, so the steps take one argument instead of a dozen."""

    question: str
    ledger: SourceLedger
    emit: ProgressFn
    deadline: float
    clock: Callable[[], float]
    used_queries: set[str] = field(default_factory=set[str])
    rounds: int = 0
    searches: int = 0
    pages_read: int = 0
    stop: StopReason = "sufficient"

    def time_left(self) -> float:
        return self.deadline - self.clock()


class DeepResearch:
    def __init__(  # noqa: PLR0913 - two ports, the guard, the budget and two injectable clocks
        self,
        *,
        model: ResearchModel,
        web: WebPort,
        guard: TextGuard,
        budget: ResearchBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._model = model
        self._web = web
        self._guard = guard
        self._budget = budget or ResearchBudget()
        self._clock = clock
        self._today = today

    async def run(self, question: str, *, progress: ProgressFn | None = None) -> FinalReport:
        budget = self._budget
        run = _Run(
            question=question,
            ledger=SourceLedger(max_sources=budget.max_pages * _HITS_KEPT_PER_PAGE_READ),
            emit=progress or _ignore,
            deadline=self._clock() + budget.max_seconds,
            clock=self._clock,
        )

        await run.emit(Progress("planning"))
        try:
            plan = await self._within(
                self._model.generate(system=PLAN_SYSTEM, user=question, schema=Plan),
                run.time_left(),
            )
        except Exception as exc:
            raise ResearchFailedError(f"could not plan the research: {exc}") from exc
        queries = _unique(plan.queries, run.used_queries)

        while True:
            run.rounds += 1
            await self._search(run, queries)
            await self._read(run)
            if run.stop == "sufficient" and run.time_left() <= 0:
                run.stop = "time"
            if run.stop in ("searches", "time"):
                break
            if run.pages_read >= budget.max_pages:
                run.stop = "pages"
                break
            if run.rounds >= budget.max_rounds:
                run.stop = "rounds"
                break
            queries = await self._review(run)
            if not queries:
                run.stop = "sufficient"
                break

        draft = await self._write(run)
        return build_report(
            question=question,
            draft=draft,
            ledger=run.ledger,
            guard=self._guard,
            as_of=self._today(),
            stopped_because=run.stop,
            rounds=run.rounds,
            searches=run.searches,
            pages_read=run.pages_read,
        )

    # ---- steps ----

    async def _search(self, run: _Run, queries: list[str]) -> None:
        budget = self._budget
        for query in queries:
            if run.searches >= budget.max_searches:
                run.stop = "searches"
                return
            if run.time_left() <= 0:
                run.stop = "time"
                return
            await run.emit(Progress("searching", query, run.rounds))
            run.searches += 1
            try:
                hits = await self._within(
                    self._web.search(query, count=budget.results_per_search), run.time_left()
                )
            except Exception:
                logger.warning("research_search_failed", round=run.rounds)
                continue
            for hit in hits:
                run.ledger.offer(hit)

    async def _read(self, run: _Run) -> None:
        budget = self._budget
        room = budget.max_pages - run.pages_read
        batch = run.ledger.unread()[: min(budget.pages_per_round, room)]
        if not batch or run.time_left() <= 0:
            return
        await run.emit(Progress("reading", str(len(batch)), run.rounds))
        gate = asyncio.Semaphore(budget.concurrent_fetches)

        async def fetch(url: str) -> FetchedPage | None:
            async with gate:
                try:
                    return await self._within(self._web.fetch(url), run.time_left())
                except Exception:
                    logger.warning("research_fetch_failed")
                    return None

        pages = await asyncio.gather(*(fetch(source.url) for source in batch))
        run.pages_read += len(batch)
        for source, page in zip(batch, pages, strict=True):
            if page is None or not page.text.strip():
                run.ledger.mark_unreadable(source.id)
            elif looks_like_injection(page.text):
                logger.warning("research_source_excluded", reason="instruction_like_text")
                run.ledger.exclude(source.id)
            else:
                run.ledger.attach(source.id, page, chars=budget.page_chars)

    async def _review(self, run: _Run) -> list[str]:
        """New queries if the model wants more, else []. A failing or slow review simply ends the
        research - the write step still has everything read so far."""
        await run.emit(Progress("reviewing", "", run.rounds))
        excerpts = render_sources(run.ledger.citeable(), chars=_REVIEW_EXCERPT_CHARS)
        try:
            review = await self._within(
                self._model.generate(
                    system=REVIEW_SYSTEM,
                    user=f"Question: {run.question}\n\nSources read so far:\n\n"
                    f"{excerpts or '(none)'}",
                    schema=GapReview,
                ),
                run.time_left(),
            )
        except Exception:
            logger.warning("research_review_failed")
            return []
        if review.sufficient:
            return []
        return _unique(review.follow_up_queries, run.used_queries)

    async def _write(self, run: _Run) -> DraftReport:
        sources = run.ledger.citeable()
        if not sources:
            return DraftReport(title=run.question)
        await run.emit(Progress("writing"))
        prompt = (
            f"Question: {run.question}\nToday: {self._today().isoformat()}\n\n"
            f"{render_sources(sources, chars=self._budget.page_chars)}"
        )
        try:
            return await self._within(
                self._model.generate(system=WRITE_SYSTEM, user=prompt, schema=DraftReport),
                max(run.time_left(), _MIN_WRITE_SECONDS),
            )
        except Exception as exc:
            raise ResearchFailedError(f"could not write the report: {exc}") from exc

    @staticmethod
    async def _within[T](awaitable: Awaitable[T], seconds: float) -> T:
        async with asyncio.timeout(max(seconds, 0.001)):
            return await awaitable


__all__ = ["DeepResearch", "Progress", "ProgressFn", "Stage"]
