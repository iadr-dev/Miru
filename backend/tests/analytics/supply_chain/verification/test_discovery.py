from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from analytics.supply_chain.domain.models import (
    Endpoint,
    Grade,
    Kind,
    Origin,
    Relation,
    Status,
)
from analytics.supply_chain.verification.directory import build_directory
from analytics.supply_chain.verification.discovery import (
    CandidateRow,
    DiscoveryConfig,
    discover_targets,
    gap_targets,
    read_candidates,
    stale_targets,
)
from analytics.supply_chain.verification.models import TargetSource
from analytics.supply_chain.verification.rejected_log import (
    append_rejection,
    load_recent_rejections,
    rejected_path,
)
from tests.analytics.supply_chain.verification.support import TODAY

ROSTER = {"9001": "Alpha", "9002": "Beta", "9003": "Gamma", "9004": "Delta", "9005": "Epsilon"}
VALUES = {"9001": 100.0, "9002": 900.0, "9003": 500.0, "9004": 50.0, "9005": 700.0}


def directory():
    return build_directory(roster=ROSTER, market_values=VALUES)


def row(
    supplier: str, customer: str, *, resolved: bool = True, name: str = "", kind: str = "supplies"
) -> CandidateRow:
    return CandidateRow(
        supplier=supplier,
        customer=customer,
        counterparty_name=name,
        counterparty_resolved=resolved,
        kind=Kind(kind),
    )


def relation(
    supplier: str, customer: str, *, verified: date | None, origin: Origin = Origin.RESEARCH
) -> Relation:
    return Relation(
        supplier=Endpoint(symbol=supplier, market="TW"),
        customer=Endpoint(symbol=customer, market="TW"),
        kind=Kind.SUPPLIES,
        status=Status.VERIFIED,
        grade=Grade.LOW,
        origin=origin,
        last_verified_at=datetime.combine(verified, datetime.min.time(), tzinfo=UTC)
        if verified
        else None,
    )


def discover(candidates, published=(), rejected=frozenset(), **cfg):
    return discover_targets(
        directory=directory(),
        candidates=candidates,
        published=list(published),
        rejected=set(rejected),
        today=TODAY,
        config=DiscoveryConfig(sources=frozenset({TargetSource.CANDIDATES}), **cfg),
    )


def test_both_ends_must_resolve_to_a_real_company() -> None:
    targets = discover(
        [
            row("9001", "9002"),
            row("9001", "9999"),  # not in the roster
            row("9001", "A long unresolved company name"),
            row("9001", "9003", resolved=False),  # counterparty not resolved
            row("9001", "ZZZA", name="Zed Corp"),  # a US ticker, resolved
            row("9001", "zzza", name="lower"),  # not a ticker
        ]
    )
    assert {t.claim_id for t in targets} == {
        "supplies:TW-9001>TW-9002",
        "supplies:TW-9001>US-ZZZA",
    }
    us = next(t for t in targets if t.customer and t.customer.market == "US")
    assert us.customer is not None
    assert us.customer.names == ("Zed Corp",)


def test_ranked_by_the_larger_companys_market_value() -> None:
    targets = discover(
        [row("9004", "9001"), row("9001", "9002"), row("9003", "9004"), row("9005", "9001")]
    )
    assert [t.claim_id for t in targets] == [
        "supplies:TW-9001>TW-9002",  # 900
        "supplies:TW-9005>TW-9001",  # 700
        "supplies:TW-9003>TW-9004",  # 500
        "supplies:TW-9004>TW-9001",  # 100
    ]


def test_duplicates_self_pairs_and_the_limit() -> None:
    targets = discover(
        [row("9001", "9002"), row("9001", "9002"), row("9001", "9001"), row("9003", "9004")],
        limit=1,
    )
    assert [t.claim_id for t in targets] == ["supplies:TW-9001>TW-9002"]


def test_already_published_pairs_are_skipped() -> None:
    published = [relation("9001", "9002", verified=TODAY)]
    targets = discover([row("9001", "9002"), row("9003", "9004")], published)
    assert [t.claim_id for t in targets] == ["supplies:TW-9003>TW-9004"]


def test_recently_rejected_claims_are_skipped_until_the_cooldown_passes(tmp_path: Path) -> None:
    path = rejected_path(tmp_path)
    append_rejection(
        path, claim_id="supplies:TW-9001>TW-9002", on=TODAY - timedelta(days=10), reason="x"
    )
    append_rejection(
        path, claim_id="supplies:TW-9003>TW-9004", on=TODAY - timedelta(days=200), reason="x"
    )
    path.write_text(path.read_text(encoding="utf-8") + "not json\n{}\n", encoding="utf-8")
    recent = load_recent_rejections(path, today=TODAY, cooldown_days=90)
    assert recent == {"supplies:TW-9001>TW-9002"}
    targets = discover([row("9001", "9002"), row("9003", "9004")], rejected=recent)
    assert [t.claim_id for t in targets] == ["supplies:TW-9003>TW-9004"]
    assert load_recent_rejections(path, today=TODAY, cooldown_days=365) == {
        "supplies:TW-9001>TW-9002",
        "supplies:TW-9003>TW-9004",
    }


def test_rejection_log_is_append_only(tmp_path: Path) -> None:
    path = rejected_path(tmp_path)
    append_rejection(path, claim_id="a", on=TODAY, reason="r1")
    append_rejection(path, claim_id="b", on=TODAY, reason="r2")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["claim_id"] for line in lines] == ["a", "b"]


def test_candidates_file_reader_skips_bad_lines(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    good = {
        "supplier": "9001",
        "customer": "9002",
        "counterparty_name": "n",
        "counterparty_resolved": True,
        "kind": "supplies",
    }
    path.write_text(
        "\n".join(
            [
                json.dumps(good),
                "",
                "not json",
                json.dumps({"supplier": 1}),
                json.dumps(good | {"kind": "x"}),
            ]
        ),
        encoding="utf-8",
    )
    assert len(read_candidates(path)) == 1
    assert read_candidates(tmp_path / "missing.jsonl") == []


def test_gaps_are_the_biggest_companies_without_any_relation() -> None:
    published = [relation("9002", "9003", verified=TODAY)]
    targets = gap_targets(directory(), published, top_n=3)
    # top 3 by value: 9002, 9005, 9003 -> 9002 and 9003 are covered
    assert [t.claim_id for t in targets] == ["gap:TW-9005"]
    assert targets[0].is_gap
    assert targets[0].customer is None


def test_stale_rechecks_pick_old_or_never_verified_research_rows_only() -> None:
    old = TODAY - timedelta(days=400)
    published = [
        relation("9001", "9002", verified=old),
        relation("9003", "9004", verified=TODAY - timedelta(days=10)),
        relation("9005", "9001", verified=None),
        relation("9002", "9004", verified=old, origin=Origin.MANUAL),
    ]
    targets = stale_targets(directory(), published, today=TODAY, reverify_days=365)
    assert {t.claim_id for t in targets} == {"supplies:TW-9001>TW-9002", "supplies:TW-9005>TW-9001"}
    assert all(t.source is TargetSource.STALE for t in targets)


def test_all_sources_share_the_limit_and_unused_room_flows_on() -> None:
    cfg = DiscoveryConfig(limit=10, gap_top_n=5)
    published = [relation("9001", "9002", verified=None)]
    mixed = discover_targets(
        directory=directory(),
        candidates=[row("9003", "9004"), row("9005", "9004")],
        published=published,
        rejected=set(),
        today=TODAY,
        config=cfg,
    )
    sources = [t.source for t in mixed]
    assert sources == [
        TargetSource.CANDIDATES,
        TargetSource.CANDIDATES,
        TargetSource.GAPS,
        TargetSource.GAPS,
        TargetSource.GAPS,
        TargetSource.STALE,
    ]
    tight = discover_targets(
        directory=directory(),
        candidates=[row(f"900{i}", "9001") for i in (2, 3, 4, 5)],
        published=published,
        rejected=set(),
        today=TODAY,
        config=DiscoveryConfig(limit=4, gap_top_n=5),
    )
    assert len(tight) == 4
    assert {t.source for t in tight} >= {TargetSource.CANDIDATES, TargetSource.GAPS}
