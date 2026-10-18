from __future__ import annotations

import json
from pathlib import Path

import yaml

from analytics.supply_chain.domain.models import Grade, Status, Trust
from analytics.supply_chain.verification.budget import Budget, Limits
from analytics.supply_chain.verification.directory import build_directory
from analytics.supply_chain.verification.models import OutcomeKind, Target, TargetSource
from analytics.supply_chain.verification.pipeline import Pipeline, PipelineConfig, run_pipeline
from analytics.supply_chain.verification.rejected_log import rejected_path
from analytics.supply_chain.verification.search import find_hits, pick_hits, queries_for
from analytics.supply_chain.verification.writer import RelationWriter
from tests.analytics.supply_chain.verification.support import (
    ALPHA,
    BETA,
    PAGE_TEXT,
    TODAY,
    StubSearch,
    hit,
    judgement_json,
    llm,
    target,
)

TRUST = {
    "ir.example.com": Trust.PRIMARY,
    "wire-a.example.com": Trust.SECONDARY,
    "wire-b.example.com": Trust.SECONDARY,
    "forum.example.org": Trust.REJECTED,
}
DIRECTORY = build_directory(roster={"9001": "阿爾法零件", "9002": "貝塔裝置"}, market_values={})


def pipeline(  # noqa: PLR0913 - test builder with optional knobs
    search: StubSearch,
    client: object,
    *,
    limits: Limits | None = None,
    second: object | None = None,
    second_opinion: bool = False,
    pages: int = 3,
) -> Pipeline:
    return Pipeline(
        search=search,
        judge_client=client,  # type: ignore[arg-type]
        second_client=second,  # type: ignore[arg-type]
        directory=DIRECTORY,
        budget=Budget(limits or Limits()),
        today=TODAY,
        config=PipelineConfig(pages_per_claim=pages, second_opinion=second_opinion),
        trust_table=TRUST,
    )


def pages(*hosts: str) -> StubSearch:
    urls = [f"https://{h}/p" for h in hosts]
    return StubSearch([hit(u) for u in urls], dict.fromkeys(urls, PAGE_TEXT))


async def test_two_independent_secondary_sources_grade_high() -> None:
    p = pipeline(
        pages("wire-a.example.com", "wire-b.example.com"), llm(judgement_json(), judgement_json())
    )
    outcome = await p.verify_target(target())
    assert outcome.kind is OutcomeKind.VERIFIED
    [result] = outcome.results
    assert result.grade is Grade.HIGH and result.status is Status.VERIFIED
    assert {e.publisher for e in result.evidence} == {"wire-a.example.com", "wire-b.example.com"}
    assert str(result.as_of) == "2026-09-01"


async def test_a_primary_source_stops_the_search_early() -> None:
    search = pages("ir.example.com", "wire-a.example.com")
    client = llm(judgement_json(), judgement_json())
    p = pipeline(search, client)
    outcome = await p.verify_target(target())
    assert outcome.results[0].grade is Grade.HIGH
    assert search.fetched == ["https://ir.example.com/p"]
    assert len(client.calls) == 1


async def test_single_tertiary_source_is_published_with_a_low_grade() -> None:
    p = pipeline(pages("blog.example.net"), llm(judgement_json()))
    outcome = await p.verify_target(target())
    assert outcome.results[0].grade is Grade.LOW


async def test_pages_that_fail_the_guards_make_a_rejected_claim() -> None:
    p = pipeline(
        pages("wire-a.example.com", "wire-b.example.com"),
        llm(judgement_json(current=False), judgement_json(excerpt="nope")),
    )
    outcome = await p.verify_target(target())
    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.reason in {"not_current", "excerpt_not_in_page"}
    assert p.reasons["not_current"] == 1 and p.reasons["excerpt_not_in_page"] == 1


async def test_no_hits_is_rejected_and_a_down_provider_is_skipped() -> None:
    assert (await pipeline(StubSearch([]), llm()).verify_target(target())).reason == "no_sources"
    outcome = await pipeline(StubSearch(None), llm()).verify_target(target())
    assert outcome.kind is OutcomeKind.SKIPPED and outcome.reason == "search_unavailable"


async def test_rejected_publishers_are_never_fetched_and_tiers_are_preferred() -> None:
    hits = [
        hit("https://forum.example.org/a"),
        hit("https://blog.example.net/b"),
        hit("https://ir.example.com/c"),
        hit("https://wire-a.example.com/d"),
    ]
    picked = pick_hits(hits, limit=3, trust_table=TRUST)
    assert [h.url for h in picked] == [
        "https://ir.example.com/c",
        "https://wire-a.example.com/d",
        "https://blog.example.net/b",
    ]
    assert len(pick_hits(hits + hits, limit=10, trust_table=TRUST)) == 3  # duplicates dropped


async def test_pages_per_claim_is_a_cap() -> None:
    search = pages("a.example.net", "b.example.net", "c.example.net", "d.example.net")
    client = llm(*[judgement_json(supports=False)] * 4)
    p = pipeline(search, client, pages=2)
    await p.verify_target(target())
    assert len(search.fetched) == 2


async def test_queries_use_both_languages() -> None:
    queries = queries_for(target())
    assert "阿爾法零件" in queries[0] and "貝塔裝置" in queries[0]
    assert "Alpha Parts Co., Ltd." in queries[1] and "Beta Devices Inc." in queries[1]
    gap = target().model_copy(update={"customer": None})
    assert "主要客戶" in queries_for(gap)[0]


async def test_search_budget_stops_the_search() -> None:
    search = StubSearch([hit("https://a.example.net/p")], {})
    budget = Budget(Limits(max_searches=1))
    hits = await find_hits(search, target(), budget, pages_per_claim=3)
    assert hits is not None and len(search.queries) == 1
    assert budget.stopped_by == "searches"


async def test_page_budget_ends_the_claim_as_skipped() -> None:
    p = pipeline(pages("a.example.net"), llm(judgement_json()), limits=Limits(max_pages=0))
    outcome = await p.verify_target(target())
    assert outcome.kind is OutcomeKind.SKIPPED and p.budget.stopped_by == "pages"


async def test_second_opinion_disagreement_downgrades_to_candidate() -> None:
    search = pages("blog.example.net")
    p = pipeline(
        search,
        llm(judgement_json()),
        second=llm(judgement_json(supports=False)),
        second_opinion=True,
    )
    outcome = await p.verify_target(target())
    assert outcome.results[0].status is Status.CANDIDATE
    agree = pipeline(
        search, llm(judgement_json()), second=llm(judgement_json()), second_opinion=True
    )
    assert (await agree.verify_target(target())).results[0].status is Status.VERIFIED


async def test_second_opinion_is_off_by_default_and_skipped_for_strong_claims() -> None:
    second = llm(judgement_json(supports=False))
    off = pipeline(pages("blog.example.net"), llm(judgement_json()), second=second)
    assert (await off.verify_target(target())).results[0].status is Status.VERIFIED
    assert second.calls == []
    strong = pipeline(
        pages("ir.example.com"), llm(judgement_json()), second=second, second_opinion=True
    )
    assert (await strong.verify_target(target())).results[0].status is Status.VERIFIED
    assert second.calls == []


async def test_run_writes_verified_claims_and_logs_rejections(tmp_path: Path) -> None:
    relations = tmp_path / "supply_chain" / "relations"
    ok, bad = target(), target(BETA, ALPHA)
    search = StubSearch(
        [hit("https://wire-a.example.com/p")], {"https://wire-a.example.com/p": PAGE_TEXT}
    )
    client = llm(judgement_json(), judgement_json(supports=False))
    p = pipeline(search, client)
    writer = RelationWriter(relations, is_leader=lambda: True, today=TODAY, trust_table=TRUST)
    summary = await run_pipeline(
        [ok, bad],
        p,
        writer=writer,
        rejected_file=rejected_path(tmp_path),
        today=TODAY,
        dry_run=False,
    )
    assert (summary.claims_tried, summary.verified, summary.rejected) == (2, 1, 1)
    assert summary.rows_written == 1 and summary.stopped_by is None
    assert summary.outcomes == {ok.claim_id: "verified", bad.claim_id: "rejected"}
    [row] = yaml.safe_load((relations / "TW-9001.yaml").read_text(encoding="utf-8"))
    assert row["origin"] == "research" and row["evidence"][0]["checker"] == "llm"
    logged = [
        json.loads(x) for x in rejected_path(tmp_path).read_text(encoding="utf-8").splitlines()
    ]
    assert [x["claim_id"] for x in logged] == [bad.claim_id]


async def test_dry_run_writes_and_logs_nothing(tmp_path: Path) -> None:
    relations = tmp_path / "supply_chain" / "relations"
    search = StubSearch(
        [hit("https://wire-a.example.com/p")], {"https://wire-a.example.com/p": PAGE_TEXT}
    )
    p = pipeline(search, llm(judgement_json(), judgement_json(supports=False)))
    writer = RelationWriter(
        relations, is_leader=lambda: True, today=TODAY, trust_table=TRUST, dry_run=True
    )
    summary = await run_pipeline(
        [target(), target(BETA, ALPHA)],
        p,
        writer=writer,
        rejected_file=rejected_path(tmp_path),
        today=TODAY,
        dry_run=True,
    )
    assert summary.verified == 1 and summary.rows_written == 1 and summary.dry_run
    assert not (tmp_path / "supply_chain").exists()


async def test_claim_cap_stops_the_run_gracefully() -> None:
    search = pages("wire-a.example.com")
    p = pipeline(search, llm(*[judgement_json(supports=False)] * 3), limits=Limits(max_claims=2))
    targets = [
        target(),
        target(BETA, ALPHA),
        target(ALPHA, BETA).model_copy(update={"claim_id": "x"}),
    ]
    summary = await run_pipeline(
        targets, p, writer=None, rejected_file=None, today=TODAY, dry_run=True
    )
    assert summary.claims_tried == 2 and summary.stopped_by == "claims"


async def test_spend_cap_stops_the_run_and_is_reported() -> None:
    search = pages("wire-a.example.com")
    p = pipeline(search, llm(*[judgement_json(supports=False)] * 5), limits=Limits(budget_usd=0.03))
    targets = [target().model_copy(update={"claim_id": f"c{i}"}) for i in range(5)]
    summary = await run_pipeline(
        targets, p, writer=None, rejected_file=None, today=TODAY, dry_run=True
    )
    assert summary.stopped_by == "budget_usd"
    assert summary.claims_tried < 5
    assert 0 < summary.spend_usd <= 0.04


async def test_llm_cap_stops_the_run() -> None:
    search = pages("wire-a.example.com")
    p = pipeline(search, llm(*[judgement_json(supports=False)] * 5), limits=Limits(max_llm_calls=1))
    targets = [target().model_copy(update={"claim_id": f"c{i}"}) for i in range(4)]
    summary = await run_pipeline(
        targets, p, writer=None, rejected_file=None, today=TODAY, dry_run=True
    )
    assert summary.stopped_by == "llm_calls" and summary.llm_calls == 1


async def test_budget_skipped_claims_are_not_logged_as_rejected(tmp_path: Path) -> None:
    search = pages("wire-a.example.com")
    p = pipeline(search, llm(judgement_json()), limits=Limits(max_llm_calls=0))
    summary = await run_pipeline(
        [target()],
        p,
        writer=None,
        rejected_file=rejected_path(tmp_path),
        today=TODAY,
        dry_run=False,
    )
    assert summary.skipped == 1 and summary.rejected == 0
    assert not rejected_path(tmp_path).exists()


async def test_gap_target_turns_a_page_into_concrete_claims() -> None:
    search = pages("wire-a.example.com")
    reply = json.dumps(
        [
            {
                "counterparty": "貝塔裝置",
                "role": "customer",
                "excerpt": "阿爾法零件目前持續供貨給貝塔裝置，為其主要供應商之一。",
            }
        ],
        ensure_ascii=False,
    )
    gap = Target(claim_id="gap:TW-9001", source=TargetSource.GAPS, supplier=ALPHA, priority=1.0)
    p = pipeline(search, llm(reply))
    outcome = await p.verify_target(gap)
    assert outcome.kind is OutcomeKind.VERIFIED
    [result] = outcome.results
    assert (result.supplier.symbol, result.customer.symbol) == ("9001", "9002")
    assert result.evidence[0].checker.value == "llm"
