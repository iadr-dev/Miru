"""Sub-questions run side by side, share one budget, and one failing lane cannot sink the run."""

from __future__ import annotations

from datetime import date

import pytest

from research import DeepResearch, ResearchBudget
from research.models import Claim, DraftReport, DraftSection, GapReview, Plan, SubQuestion
from tests.research.fakes import FakeWeb, Recorder, ScriptedModel, hit, pages_for

pytestmark = pytest.mark.governance

TODAY = date(2026, 10, 6)


def _plan(*pairs: tuple[str, str]) -> Plan:
    return Plan(sub_questions=[SubQuestion(question=q, queries=[query]) for q, query in pairs])


def _draft(source_id: int = 1) -> DraftReport:
    claim = Claim(text="Revenue grew 12%.", source_ids=[source_id])
    return DraftReport(
        title="Report",
        summary=[claim],
        sections=[DraftSection(heading="Revenue", claims=[claim])],
    )


def _model(plan: Plan) -> ScriptedModel:
    return ScriptedModel(
        {
            Plan: [plan],
            GapReview: [GapReview(sufficient=True)],
            DraftReport: [_draft()],
        }
    )


def _research(model: ScriptedModel, web: FakeWeb, **budget: object) -> DeepResearch:
    return DeepResearch(
        model=model,
        web=web,
        guard=lambda text: text,
        budget=ResearchBudget(**budget) if budget else None,  # type: ignore[arg-type]
        today=lambda: TODAY,
    )


async def test_sub_questions_are_researched_at_the_same_time() -> None:
    web = FakeWeb(
        {"alpha": [hit(1)], "beta": [hit(2)], "gamma": [hit(3)]},
        pages_for([hit(1), hit(2), hit(3)]),
        delay=0.05,
    )
    model = _model(_plan(("a", "alpha"), ("b", "beta"), ("c", "gamma")))
    report = await _research(model, web).run("question")
    assert sorted(web.search_calls) == ["alpha", "beta", "gamma"]
    assert web.max_concurrent_searches == 3  # one after another would never exceed 1
    assert report.has_content


async def test_each_sub_question_reports_progress_on_its_own_lane() -> None:
    web = FakeWeb({"q1": [hit(1)], "q2": [hit(2)]}, pages_for([hit(1), hit(2)]))
    recorder = Recorder()
    await _research(_model(_plan(("a", "q1"), ("b", "q2"))), web).run("question", progress=recorder)
    assert set(recorder.lanes("searching")) == {0, 1}
    assert set(recorder.lanes("reviewing")) == {0, 1}
    assert set(recorder.lanes("planning")) == {-1}
    assert set(recorder.lanes("writing")) == {-1}
    assert "planned" in recorder.stages()


async def test_a_single_sub_question_does_not_announce_a_plan() -> None:
    web = FakeWeb({"q1": [hit(1)]}, pages_for([hit(1)]))
    recorder = Recorder()
    await _research(_model(_plan(("a", "q1"))), web).run("question", progress=recorder)
    assert "planned" not in recorder.stages()


async def test_the_search_budget_is_shared_so_parallel_lanes_cannot_overshoot() -> None:
    names = [f"q{i}" for i in range(5)]
    plan = Plan(
        sub_questions=[
            SubQuestion(question=n, queries=[n, f"{n} again", f"{n} more"]) for n in names
        ]
    )
    found = {n: [hit(i + 1)] for i, n in enumerate(names)}
    web = FakeWeb(found, pages_for([h for hits in found.values() for h in hits]), delay=0.01)
    report = await _research(_model(plan), web, max_searches=4).run("question")
    assert len(web.search_calls) == 4
    assert report.searches == 4
    assert report.stopped_because == "searches"


async def test_the_page_budget_is_shared_too_and_no_page_is_read_twice() -> None:
    first, second = [hit(i) for i in range(1, 5)], [hit(i) for i in range(5, 9)]
    web = FakeWeb({"a": first, "b": second}, pages_for(first + second), delay=0.01)
    report = await _research(
        _model(_plan(("one", "a"), ("two", "b"))), web, max_pages=3, pages_per_round=4
    ).run("question")
    assert len(web.fetch_calls) == 3
    assert len(set(web.fetch_calls)) == 3
    assert report.pages_read == 3


async def test_lanes_that_find_the_same_page_read_it_once() -> None:
    shared = [hit(1), hit(2)]
    web = FakeWeb({"a": shared, "b": shared}, pages_for(shared), delay=0.01)
    await _research(_model(_plan(("one", "a"), ("two", "b"))), web).run("question")
    assert sorted(web.fetch_calls) == sorted(h.url for h in shared)


async def test_a_lane_whose_search_fails_leaves_the_others_to_finish() -> None:
    web = FakeWeb({"good": [hit(1)]}, pages_for([hit(1)]), fail_search={"bad"})
    report = await _research(_model(_plan(("one", "bad"), ("two", "good"))), web).run("question")
    assert report.has_content
    assert [s.id for s in report.sources] == [1]


async def test_the_same_query_in_two_lanes_is_searched_once() -> None:
    web = FakeWeb({"same": [hit(1)]}, pages_for([hit(1)]))
    await _research(_model(_plan(("one", "same"), ("two", "Same"))), web).run("question")
    assert web.search_calls == ["same"]


async def test_a_larger_run_uses_the_larger_default_budget() -> None:
    budget = ResearchBudget()
    assert (budget.max_searches, budget.max_pages, budget.max_data_calls) == (24, 30, 12)
    assert budget.max_seconds == 180.0
