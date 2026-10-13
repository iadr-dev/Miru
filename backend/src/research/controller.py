"""The deep-research loop: understand -> plan -> (sub-questions in parallel) -> write.

**Code runs the loop; the model is only asked to restate, to plan, to judge "is this enough?",
and to write.** Rounds, searches, pages, data lookups, wall-clock time and the stop rule are all
decided here, so a model that never says "done" cannot run up cost, and every exit - sufficient,
ceiling hit, out of time - still ends in a report built from whatever was actually read.

A question is split into up to five independent sub-questions. Each one runs its own
search -> read -> review cycles *at the same time as the others*, against one shared source ledger
and one shared budget. Alongside web pages, a sub-question may ask for Miru's own market data (a
fixed, read-only list - see `research.datatools`); those results are cited like any other source.

Fetched text is untrusted (see `research.untrusted`), citations are enforced structurally (see
`research.report`), and the finished text goes through the governance output guard.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import structlog

from llm.base import ImagePart, Message
from research.datatools import planner_catalog
from research.ledger import SourceLedger
from research.models import (
    DataRequest,
    DraftReport,
    FinalReport,
    GapReview,
    Plan,
    ResearchBudget,
    ResearchFailedError,
    StandaloneQuestion,
    StopReason,
    SubQuestion,
)
from research.ports import DataPort, DataResult, FetchedPage, ResearchModel, WebPort
from research.report import TextGuard, build_report
from research.untrusted import (
    UNTRUSTED_NOTICE,
    clean_text,
    looks_like_injection,
    render_sources,
)

logger = structlog.get_logger(__name__)

Stage = Literal[
    "understanding", "planning", "planned", "searching", "reading", "data", "reviewing", "writing"
]

# The final write always gets at least this long, even if the research itself ran out of time:
# ending with sources read but no report would waste the whole run.
_MIN_WRITE_SECONDS = 25.0
_REVIEW_EXCERPT_CHARS = 700
_HITS_KEPT_PER_PAGE_READ = 3  # search hits kept per page we may read, so later rounds have choice
_HISTORY_MESSAGES = 6  # recent conversation shown to the restating step
_HISTORY_CHARS = 500  # per message
_IMAGE_NOTES_CHARS = 1500

_LANGUAGE = "Write in the same language as the question (Traditional Chinese if it is Chinese)."

BRIEF_SYSTEM = (
    "Rewrite the user's latest message as ONE self-contained research question. Use the earlier "
    'conversation only to resolve references ("it", "that stock", "their margins", "and last '
    "year?\"); keep the user's intent and language. If the message already stands on its own, "
    "return it unchanged. Do not answer it."
)

PLAN_SYSTEM = (
    "You plan research for an assistant that analyses Taiwan and US stocks and ETFs. Split the "
    "question into 1-5 independent sub-questions (fewer for a narrow question; each must be "
    "researchable on its own). For each, give 1-3 plain web-search queries likely to surface "
    "authoritative, recent, factual sources (company filings, exchange announcements, reputable "
    "news, official statistics). When a sub-question concerns a named listed company, you may also "
    "request up to 3 lookups in Miru's own market data - only when that data would really help, "
    "using the company's ticker (Taiwan codes like 2330, US tickers like NVDA) and only if you are "
    "sure of it. The available data tools are:\n"
    + planner_catalog()
    + "\nNever phrase queries around buying or selling. "
    + _LANGUAGE
)

REVIEW_SYSTEM = (
    "You review research progress on ONE sub-question. Given the overall question, the "
    "sub-question and the source excerpts read for it, decide whether they are enough to answer it "
    "with cited facts. If not, give up to 3 NEW search queries (and, only if useful, up to 2 "
    "market-data lookups) aimed at exactly what is missing. " + UNTRUSTED_NOTICE
)

WRITE_SYSTEM = (
    "Write a research report answering the question using ONLY the sources provided (web pages "
    "and Miru market data). Every claim must list, in source_ids, the ids of the sources that "
    "state it; omit any claim you cannot tie to a source. Give a title, 2-4 short summary claims, "
    "then "
    "sections with headings that cover each sub-question researched. State dates ('as of ...') and "
    "mention disagreement between sources. Put what the sources do not settle in open_questions. "
    "Notes about the user's attachments are context for what was asked, not evidence: never cite "
    "them. This is information, not advice: never recommend buying, selling or holding, and never "
    "give price targets or position sizes. " + _LANGUAGE + " " + UNTRUSTED_NOTICE
)


@dataclass(frozen=True)
class Progress:
    stage: Stage
    detail: str = ""
    round: int = 0
    lane: int = -1  # the sub-question this belongs to (0-based); -1 for whole-run stages
    topic: str = ""  # that sub-question's text, for the lane label


ProgressFn = Callable[[Progress], Awaitable[None]]


async def _ignore(progress: Progress) -> None:
    await asyncio.sleep(0, progress)  # a no-op listener that is still a real awaitable


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
    """The state of one research run, shared by every sub-question working on it."""

    question: str
    ledger: SourceLedger
    emit: ProgressFn
    deadline: float
    clock: Callable[[], float]
    fetch_gate: asyncio.Semaphore
    background: str = ""  # what the user's attached images show (context, never evidence)
    used_queries: set[str] = field(default_factory=set[str])
    used_data: set[tuple[str, str, str]] = field(default_factory=set[tuple[str, str, str]])
    rounds: int = 0
    searches: int = 0
    pages_read: int = 0
    data_calls: int = 0
    stop: StopReason = "sufficient"

    def time_left(self) -> float:
        return self.deadline - self.clock()

    def hit(self, reason: StopReason) -> None:
        """Record why the run ended early; the first ceiling reached is the one reported."""
        if self.stop == "sufficient":
            self.stop = reason


class DeepResearch:
    def __init__(  # noqa: PLR0913 — the ports, the guard, the budget and two injectable clocks
        self,
        *,
        model: ResearchModel,
        web: WebPort,
        guard: TextGuard,
        data: DataPort | None = None,
        budget: ResearchBudget | None = None,
        clock: Callable[[], float] = time.monotonic,
        today: Callable[[], date] = date.today,
    ) -> None:
        self._model = model
        self._web = web
        self._data = data
        self._guard = guard
        self._budget = budget or ResearchBudget()
        self._clock = clock
        self._today = today

    async def run(
        self,
        question: str,
        *,
        history: Sequence[Message] = (),
        images: Sequence[ImagePart] = (),
        progress: ProgressFn | None = None,
    ) -> FinalReport:
        budget = self._budget
        run = _Run(
            question=question,
            ledger=SourceLedger(max_sources=budget.max_pages * _HITS_KEPT_PER_PAGE_READ),
            emit=progress or _ignore,
            deadline=self._clock() + budget.max_seconds,
            clock=self._clock,
            fetch_gate=asyncio.Semaphore(budget.concurrent_fetches),
        )

        await self._understand(run, history, images)

        await run.emit(Progress("planning"))
        try:
            plan = await self._within(
                self._model.generate(system=PLAN_SYSTEM, user=self._brief(run), schema=Plan),
                run.time_left(),
            )
        except Exception as exc:
            raise ResearchFailedError(f"could not plan the research: {exc}") from exc

        subs = plan.sub_questions
        if len(subs) > 1:
            await run.emit(Progress("planned", str(len(subs))))
        results = await asyncio.gather(
            *(self._investigate(run, lane, sub) for lane, sub in enumerate(subs)),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                if not isinstance(result, Exception):
                    raise result  # a cancellation must still cancel the whole run
                logger.warning("research_subquestion_failed", error=repr(result))

        draft = await self._write(run, subs)
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
            data_calls=run.data_calls,
        )

    # ---- preparing the question ----

    async def _understand(
        self, run: _Run, history: Sequence[Message], images: Sequence[ImagePart]
    ) -> None:
        """Make the question stand alone (resolving "it", "their margins") and note what any
        attached image shows. Both are best effort: a failure leaves the run on the raw question."""
        recent = [m for m in history if m.content][-_HISTORY_MESSAGES:]
        if not recent and not images:
            return
        await run.emit(Progress("understanding"))
        if recent:
            transcript = "\n".join(
                f"{m.role.value}: {clean_text(m.content or '', _HISTORY_CHARS)}" for m in recent
            )
            try:
                restated = await self._within(
                    self._model.generate(
                        system=BRIEF_SYSTEM,
                        user=f"Conversation so far:\n{transcript}\n\n"
                        f"Latest message: {run.question}",
                        schema=StandaloneQuestion,
                    ),
                    run.time_left(),
                )
                run.question = restated.question.strip() or run.question
            except Exception:
                logger.warning("research_restate_failed")
        if images:
            try:
                notes = await self._within(
                    self._model.describe_images(images=images, question=run.question),
                    run.time_left(),
                )
            except Exception:
                logger.warning("research_image_notes_failed")
                return
            notes = clean_text(notes, _IMAGE_NOTES_CHARS)
            if looks_like_injection(notes):
                logger.warning("research_image_notes_excluded", reason="instruction_like_text")
                return
            run.background = notes

    def _brief(self, run: _Run) -> str:
        """What the planner (and the review) is told about the question."""
        parts = [f"Question: {run.question}", f"Today: {self._today().isoformat()}"]
        if run.background:
            parts.append(
                "What the user's attached images show (context only):\n"
                f"<notes>\n{run.background}\n</notes>"
            )
        return "\n".join(parts)

    # ---- one sub-question ----

    async def _investigate(self, run: _Run, lane: int, sub: SubQuestion) -> None:
        budget = self._budget
        queries = _unique(sub.queries, run.used_queries)
        data = self._fresh_data(run, sub.data)
        own: list[int] = []  # ids of the sources this sub-question read, for its own review
        for round_no in range(1, budget.max_rounds + 1):
            run.rounds = max(run.rounds, round_no)
            await self._search(run, lane, sub, round_no, queries)
            await self._lookup(run, lane, sub, round_no, data, own)
            await self._read(run, lane, sub, round_no, own)
            if run.stop in ("searches", "time") or run.time_left() <= 0:
                run.hit("time")
                return
            if run.pages_read >= budget.max_pages:
                run.hit("pages")
                return
            if round_no >= budget.max_rounds:
                run.hit("rounds")
                return
            queries, data = await self._review(run, lane, sub, round_no, own)
            if not queries and not data:
                return

    def _fresh_data(self, run: _Run, requests: Sequence[DataRequest]) -> list[DataRequest]:
        """The requests not already made by this run, so sub-questions do not repeat each other."""
        fresh: list[DataRequest] = []
        for request in requests:
            key = (request.tool, request.symbol.upper(), request.market)
            if key not in run.used_data:
                run.used_data.add(key)
                fresh.append(request)
        return fresh

    async def _search(
        self, run: _Run, lane: int, sub: SubQuestion, round_no: int, queries: list[str]
    ) -> None:
        budget = self._budget
        for query in queries:
            if run.searches >= budget.max_searches:
                run.hit("searches")
                return
            if run.time_left() <= 0:
                run.hit("time")
                return
            run.searches += 1  # claimed before any await, so parallel workers cannot overshoot
            await run.emit(Progress("searching", query, round_no, lane, sub.question))
            try:
                hits = await self._within(
                    self._web.search(query, count=budget.results_per_search), run.time_left()
                )
            except Exception:
                logger.warning("research_search_failed", round=round_no)
                continue
            for hit in hits:
                run.ledger.offer(hit)

    async def _lookup(  # noqa: PLR0913, PLR0917 — the sub-question's lane, round, what it found
        self,
        run: _Run,
        lane: int,
        sub: SubQuestion,
        round_no: int,
        requests: list[DataRequest],
        own: list[int],
    ) -> None:
        data = self._data
        if data is None or not requests:
            return
        budget = self._budget
        batch: list[DataRequest] = []
        for request in requests:
            if run.data_calls >= budget.max_data_calls or run.time_left() <= 0:
                break
            run.data_calls += 1
            batch.append(request)
        if not batch:
            return
        await run.emit(
            Progress("data", ", ".join(r.symbol for r in batch), round_no, lane, sub.question)
        )
        results = await asyncio.gather(*(self._fetch_data(run, data, r) for r in batch))
        for request, result in zip(batch, results, strict=True):
            if result is None:
                continue
            if looks_like_injection(result.text):
                logger.warning("research_data_excluded", tool=request.tool)
                continue
            source = run.ledger.add_data(title=result.title, text=result.text[: budget.page_chars])
            if source is not None:
                own.append(source.id)

    async def _fetch_data(
        self, run: _Run, data: DataPort, request: DataRequest
    ) -> DataResult | None:
        try:
            return await self._within(data.fetch(request), run.time_left())
        except Exception:
            logger.warning("research_data_failed", tool=request.tool)
            return None

    async def _read(
        self, run: _Run, lane: int, sub: SubQuestion, round_no: int, own: list[int]
    ) -> None:
        budget = self._budget
        room = budget.max_pages - run.pages_read
        if room <= 0 or run.time_left() <= 0:
            return
        batch = run.ledger.claim_unread(min(budget.pages_per_round, room))
        if not batch:
            return
        run.pages_read += len(batch)  # claimed before any await, like the searches
        await run.emit(Progress("reading", str(len(batch)), round_no, lane, sub.question))

        async def fetch(url: str) -> FetchedPage | None:
            async with run.fetch_gate:
                try:
                    return await self._within(self._web.fetch(url), run.time_left())
                except Exception:
                    logger.warning("research_fetch_failed")
                    return None

        pages = await asyncio.gather(*(fetch(source.url or "") for source in batch))
        for source, page in zip(batch, pages, strict=True):
            if page is None or not page.text.strip():
                run.ledger.mark_unreadable(source.id)
            elif looks_like_injection(page.text):
                logger.warning("research_source_excluded", reason="instruction_like_text")
                run.ledger.exclude(source.id)
            else:
                run.ledger.attach(source.id, page, chars=budget.page_chars)
                own.append(source.id)

    async def _review(
        self, run: _Run, lane: int, sub: SubQuestion, round_no: int, own: list[int]
    ) -> tuple[list[str], list[DataRequest]]:
        """New queries and lookups if the model wants more, else nothing. A failing or slow review
        simply ends this sub-question - the write step still has everything read so far."""
        await run.emit(Progress("reviewing", "", round_no, lane, sub.question))
        read = [s for i in own if (s := run.ledger.get(i)) is not None and s.citeable]
        excerpts = render_sources(read, chars=_REVIEW_EXCERPT_CHARS)
        try:
            review = await self._within(
                self._model.generate(
                    system=REVIEW_SYSTEM,
                    user=f"Overall question: {run.question}\nSub-question: {sub.question}\n\n"
                    f"Sources read for it:\n\n{excerpts or '(none)'}",
                    schema=GapReview,
                ),
                run.time_left(),
            )
        except Exception:
            logger.warning("research_review_failed")
            return [], []
        if review.sufficient:
            return [], []
        return (
            _unique(review.follow_up_queries, run.used_queries),
            self._fresh_data(run, review.follow_up_data),
        )

    # ---- the report ----

    async def _write(self, run: _Run, subs: list[SubQuestion]) -> DraftReport:
        sources = run.ledger.citeable()
        if not sources:
            return DraftReport(title=run.question)
        await run.emit(Progress("writing"))
        outline = "\n".join(f"{i}. {sub.question}" for i, sub in enumerate(subs, start=1))
        prompt = (
            f"{self._brief(run)}\n\nSub-questions researched:\n{outline}\n\n"
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
