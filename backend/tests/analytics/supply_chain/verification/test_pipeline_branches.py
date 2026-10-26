"""Characterization of the verification pipeline's branches: the pair and gap loops and the
run loop (write failures, leader loss, candidate counting, rejection logging)."""

from __future__ import annotations

import json
from pathlib import Path

from analytics.supply_chain.domain.models import Status
from analytics.supply_chain.verification.budget import Limits
from analytics.supply_chain.verification.models import (
    ClaimResult,
    OutcomeKind,
    Target,
    TargetSource,
)
from analytics.supply_chain.verification.pipeline import run_pipeline
from analytics.supply_chain.verification.rejected_log import rejected_path
from analytics.supply_chain.verification.writer import (
    NotLeaderError,
    RelationWriter,
    WriteReport,
    WriterError,
)
from tests.analytics.supply_chain.verification.support import (
    ALPHA,
    PAGE_TEXT,
    SENTENCE,
    TODAY,
    StubSearch,
    hit,
    judgement_json,
    llm,
    target,
)
from tests.analytics.supply_chain.verification.test_pipeline import TRUST, pages, pipeline

GAP = Target(claim_id="gap:TW-9001", source=TargetSource.GAPS, supplier=ALPHA, priority=1.0)


def _gap_reply(*roles: str) -> str:
    return json.dumps(
        [{"counterparty": "貝塔裝置", "role": r, "excerpt": SENTENCE} for r in roles],
        ensure_ascii=False,
    )


class _FakeWriter:
    def __init__(self, *, error: Exception | None = None, rows: int = 1) -> None:
        self.error = error
        self.rows = rows
        self.applied: list[tuple[ClaimResult, ...]] = []

    def apply(self, results: tuple[ClaimResult, ...]) -> WriteReport:
        if self.error is not None:
            raise self.error
        self.applied.append(results)
        return WriteReport(created=self.rows)


def _t(i: int) -> Target:
    return target().model_copy(update={"claim_id": f"c{i}"})


# --- _verify_pair ---------------------------------------------------------------------------


async def test_pair_failed_fetch_is_counted_and_the_next_hit_is_tried() -> None:
    search = StubSearch(
        [hit("https://gone.example.net/p"), hit("https://wire-a.test/p")],
        {"https://wire-a.test/p": PAGE_TEXT},
    )
    p = pipeline(search, llm(judgement_json()))
    outcome = await p.verify_target(target())
    assert outcome.kind is OutcomeKind.VERIFIED
    assert p.reasons["fetch_failed"] == 1
    assert search.fetched == ["https://wire-a.test/p", "https://gone.example.net/p"]


async def test_pair_page_cap_hit_mid_claim_ends_it_with_the_top_reason() -> None:
    search = pages("a.example.net", "b.example.net")
    p = pipeline(search, llm(judgement_json(current=False)), limits=Limits(max_pages=1))
    outcome = await p.verify_target(target())
    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.reason == "not_current"
    assert search.fetched == ["https://a.example.net/p"]
    assert p.budget.stopped_by == "pages"


async def test_pair_llm_cap_hit_after_one_examined_page_is_rejected_not_skipped() -> None:
    search = pages("a.example.net", "b.example.net")
    p = pipeline(search, llm(judgement_json(current=False)), limits=Limits(max_llm_calls=1))
    outcome = await p.verify_target(target())
    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.reason == "not_current"
    assert p.reasons["budget"] == 1


async def test_pair_failed_fetches_only_end_as_rejected_with_fetch_failed() -> None:
    p = pipeline(StubSearch([hit("https://gone.example.net/p")], {}), llm())
    outcome = await p.verify_target(target())
    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.reason == "fetch_failed"


async def test_pair_keeps_collecting_until_high_and_uses_year_when_undated() -> None:
    undated = "阿爾法零件目前持續供貨給貝塔裝置，為其主要供應商之一。"
    urls = ["https://a.example.net/p", "https://b.example.net/p"]
    search = StubSearch([hit(u) for u in urls], dict.fromkeys(urls, undated))
    p = pipeline(search, llm(judgement_json(as_of_year=2025), judgement_json(as_of_year=2024)))
    outcome = await p.verify_target(target())
    [result] = outcome.results
    assert len(result.evidence) == 2
    assert str(result.as_of) == "2025-01-01"
    assert result.status is Status.CANDIDATE  # same registrable domain: not independent


# --- _verify_gap ----------------------------------------------------------------------------


async def test_gap_failed_fetch_continues_and_page_reasons_are_counted() -> None:
    search = StubSearch(
        [
            hit("https://gone.example.net/p"),
            hit("https://nonames.example.net/p"),
            hit("https://wire-a.test/p"),
        ],
        {
            "https://nonames.example.net/p": "無關內容 2026-09-01",
            "https://wire-a.test/p": PAGE_TEXT,
        },
    )
    p = pipeline(search, llm(_gap_reply("customer")))
    outcome = await p.verify_target(GAP)
    assert outcome.kind is OutcomeKind.VERIFIED
    assert p.reasons["fetch_failed"] == 1
    assert p.reasons["names_not_on_page"] == 1


async def test_gap_budget_reason_stops_the_loop_and_skips_when_nothing_examined() -> None:
    search = pages("wire-a.test", "wire-b.test")
    p = pipeline(search, llm(_gap_reply("customer")), limits=Limits(max_llm_calls=0))
    outcome = await p.verify_target(GAP)
    assert outcome.kind is OutcomeKind.SKIPPED
    assert outcome.reason == "budget"
    assert p.reasons["budget"] == 1
    assert search.fetched == ["https://wire-a.test/p"]


async def test_gap_unaccepted_findings_make_a_rejected_claim() -> None:
    reply = json.dumps(
        [{"counterparty": "貝塔裝置", "role": "customer", "excerpt": "nope"}], ensure_ascii=False
    )
    p = pipeline(pages("wire-a.test"), llm(reply))
    outcome = await p.verify_target(GAP)
    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.reason == "excerpt_not_in_page"
    assert p.reasons["excerpt_not_in_page"] == 1


async def test_gap_bad_json_page_is_counted_as_its_reason() -> None:
    p = pipeline(pages("wire-a.test"), llm("not json"))
    outcome = await p.verify_target(GAP)
    assert outcome.kind is OutcomeKind.REJECTED
    assert outcome.reason == "bad_json"


async def test_gap_same_claim_from_two_pages_merges_evidence() -> None:
    p = pipeline(
        pages("wire-a.test", "wire-b.test"), llm(_gap_reply("customer"), _gap_reply("customer"))
    )
    outcome = await p.verify_target(GAP)
    [result] = outcome.results
    assert len(result.evidence) == 2
    assert {e.publisher for e in result.evidence} == {"wire-a.test", "wire-b.test"}
    assert (result.supplier.symbol, result.customer.symbol) == ("9001", "9002")
    assert result.product is None


async def test_gap_second_finding_that_fails_a_guard_is_counted_but_not_returned() -> None:
    p = pipeline(pages("wire-a.test"), llm(_gap_reply("customer", "competitor")))
    outcome = await p.verify_target(GAP)
    assert outcome.kind is OutcomeKind.VERIFIED
    assert [r.kind.value for r in outcome.results] == ["supplies"]
    assert sum(p.reasons.values()) == 1


async def test_gap_unaccepted_finding_does_not_hide_an_accepted_one() -> None:
    bad = {"counterparty": "貝塔裝置", "role": "competitor", "excerpt": "nope"}
    good = {"counterparty": "貝塔裝置", "role": "customer", "excerpt": SENTENCE}
    p = pipeline(pages("wire-a.test"), llm(json.dumps([bad, good], ensure_ascii=False)))
    outcome = await p.verify_target(GAP)
    [result] = outcome.results
    assert result.kind.value == "supplies"
    assert p.reasons["excerpt_not_in_page"] == 1


# --- run_pipeline ---------------------------------------------------------------------------


async def test_run_not_leader_stops_the_run_after_the_verified_claim_is_counted(
    tmp_path: Path,
) -> None:
    p = pipeline(pages("ir.example.com"), llm(judgement_json(), judgement_json()))
    writer = RelationWriter(
        tmp_path / "rel", is_leader=lambda: False, today=TODAY, trust_table=TRUST
    )
    summary = await run_pipeline(
        [_t(1), _t(2)], p, writer=writer, rejected_file=None, today=TODAY, dry_run=False
    )
    assert summary.stopped_by == "not_leader"
    assert (summary.claims_tried, summary.verified, summary.rows_written) == (1, 1, 0)
    assert summary.outcomes == {"c1": "verified"}


async def test_run_writer_error_is_counted_and_the_run_continues() -> None:
    p = pipeline(pages("ir.example.com"), llm(judgement_json(), judgement_json()))
    writer = _FakeWriter(error=WriterError("disk"))
    summary = await run_pipeline(
        [_t(1), _t(2)],
        p,
        writer=writer,  # type: ignore[arg-type]
        rejected_file=None,
        today=TODAY,
        dry_run=False,
    )
    assert summary.claims_tried == 2
    assert summary.verified == 2
    assert summary.rows_written == 0
    assert summary.reasons == {"write_failed": 2}
    assert summary.stopped_by is None


async def test_run_not_leader_error_from_the_writer_stops_with_that_reason() -> None:
    p = pipeline(pages("ir.example.com"), llm(judgement_json(), judgement_json()))
    writer = _FakeWriter(error=NotLeaderError("no"))
    summary = await run_pipeline(
        [_t(1), _t(2)],
        p,
        writer=writer,  # type: ignore[arg-type]
        rejected_file=None,
        today=TODAY,
        dry_run=False,
    )
    assert summary.stopped_by == "not_leader"
    assert summary.claims_tried == 1
    assert summary.reasons == {}


async def test_run_counts_candidates_apart_from_verified_and_writes_both() -> None:
    p = pipeline(pages("unlisted.example.net"), llm(judgement_json()))
    writer = _FakeWriter(rows=3)
    summary = await run_pipeline(
        [_t(1)],
        p,
        writer=writer,  # type: ignore[arg-type]
        rejected_file=None,
        today=TODAY,
        dry_run=False,
    )
    assert (summary.verified, summary.candidates, summary.rows_written) == (0, 1, 3)
    assert len(writer.applied) == 1
    assert writer.applied[0][0].status is Status.CANDIDATE


async def test_run_without_a_writer_still_counts_verified() -> None:
    p = pipeline(pages("ir.example.com"), llm(judgement_json()))
    summary = await run_pipeline(
        [_t(1)], p, writer=None, rejected_file=None, today=TODAY, dry_run=False
    )
    assert (summary.verified, summary.rows_written) == (1, 0)


async def test_run_rejection_is_logged_only_with_a_file_and_not_in_dry_run(tmp_path: Path) -> None:
    bad = llm(*[judgement_json(supports=False)] * 3)
    p = pipeline(pages("wire-a.test"), bad)
    path = rejected_path(tmp_path)
    await run_pipeline([_t(1)], p, writer=None, rejected_file=path, today=TODAY, dry_run=True)
    assert not path.exists()
    await run_pipeline([_t(2)], p, writer=None, rejected_file=None, today=TODAY, dry_run=False)
    assert not path.exists()
    summary = await run_pipeline(
        [_t(3)], p, writer=None, rejected_file=path, today=TODAY, dry_run=False
    )
    assert summary.rejected == 1
    [row] = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    assert row["claim_id"] == "c3"


async def test_run_skipped_claims_and_budget_stop_are_reported() -> None:
    p = pipeline(StubSearch(None), llm(), limits=Limits(max_claims=5))
    summary = await run_pipeline(
        [_t(1), _t(2)], p, writer=None, rejected_file=None, today=TODAY, dry_run=True
    )
    assert (summary.skipped, summary.claims_tried) == (2, 2)
    assert summary.outcomes == {"c1": "skipped", "c2": "skipped"}
    assert summary.reasons == {"search_unavailable": 2}


async def test_run_exhausted_budget_breaks_after_the_claim_in_flight() -> None:
    p = pipeline(pages("wire-a.test"), llm(judgement_json()), limits=Limits(max_llm_calls=0))
    summary = await run_pipeline(
        [_t(1), _t(2)], p, writer=None, rejected_file=None, today=TODAY, dry_run=True
    )
    assert summary.claims_tried == 1
    assert summary.stopped_by == "llm_calls"
    assert summary.skipped == 1
