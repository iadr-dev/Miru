from __future__ import annotations

import os
import time
from datetime import date
from pathlib import Path

import pytest
import yaml

from analytics.supply_chain.domain.models import Checker, Evidence, Grade, Kind, Status, Trust
from analytics.supply_chain.refdata_io.loader import parse_relation_files
from analytics.supply_chain.verification.models import ClaimResult
from analytics.supply_chain.verification.writer import (
    LOCK_NAME,
    NotLeaderError,
    RelationWriter,
    WriterError,
)
from tests.analytics.supply_chain.verification.support import ALPHA, BETA, GAMMA, TODAY

TRUST = {"news.example.com": Trust.SECONDARY, "wire.example.com": Trust.SECONDARY}


def evidence(
    url: str = "https://news.example.com/a", publisher: str = "news.example.com"
) -> Evidence:
    return Evidence(
        url=url,
        publisher=publisher,
        published_on=date(2026, 9, 1),
        excerpt="阿爾法零件目前供貨給貝塔裝置",
        trust=Trust.SECONDARY,
        checker=Checker.LLM,
    )


def result(*evid: Evidence, status: Status = Status.VERIFIED, **kw: object) -> ClaimResult:
    fields: dict[str, object] = {
        "supplier": ALPHA,
        "customer": BETA,
        "kind": Kind.SUPPLIES,
        "status": status,
        "grade": Grade.LOW,
        "as_of": date(2026, 9, 1),
        "evidence": evid or (evidence(),),
    }
    fields.update(kw)
    return ClaimResult.model_validate(fields)


def writer(directory: Path, *, leader: bool = True, dry_run: bool = False) -> RelationWriter:
    return RelationWriter(
        directory, is_leader=lambda: leader, today=TODAY, trust_table=TRUST, dry_run=dry_run
    )


def rows(path: Path) -> list[dict[str, object]]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_creates_a_supplier_file_the_loader_accepts(tmp_path: Path) -> None:
    report = writer(tmp_path).apply([result()])
    assert (report.created, report.updated) == (1, 0)
    assert report.files == ["TW-9001.yaml"]
    parsed = parse_relation_files(tmp_path, TRUST, TODAY)
    [relation] = parsed.relations
    assert relation.status is Status.VERIFIED
    assert relation.grade is Grade.MEDIUM or relation.grade is Grade.LOW
    assert relation.evidence[0].checker.value == "llm"
    assert relation.last_verified_at is not None
    assert not list(tmp_path.glob(".*tmp*")) and not (tmp_path / LOCK_NAME).exists()


def test_upsert_merges_evidence_by_url_and_moves_dates_forward(tmp_path: Path) -> None:
    writer(tmp_path).apply([result()])
    later = result(
        evidence(),  # same url: not duplicated
        evidence("https://wire.example.com/b", "wire.example.com"),
        as_of=date(2026, 10, 1),
    )
    report = writer(tmp_path).apply([later])
    assert (report.created, report.updated) == (0, 1)
    [row] = rows(tmp_path / "TW-9001.yaml")
    assert [e["url"] for e in row["evidence"]] == [  # type: ignore[index]
        "https://news.example.com/a",
        "https://wire.example.com/b",
    ]
    assert row["as_of"] == date(2026, 10, 1)
    assert row["last_verified_at"] == TODAY
    assert parse_relation_files(tmp_path, TRUST, TODAY).relations[0].grade is Grade.HIGH


def test_an_identical_result_changes_nothing(tmp_path: Path) -> None:
    writer(tmp_path).apply([result()])
    before = (tmp_path / "TW-9001.yaml").read_bytes()
    report = writer(tmp_path).apply([result()])
    assert report.unchanged == 1 and report.rows_written == 0
    assert (tmp_path / "TW-9001.yaml").read_bytes() == before


@pytest.mark.parametrize("origin", ["manual", "seeded"])
def test_manual_and_seeded_rows_are_never_touched(tmp_path: Path, origin: str) -> None:
    extra = "grade: low\n" if origin == "seeded" else ""
    existing = (
        "- supplier: {symbol: '9001', market: TW, name: A}\n"
        "  customer: {symbol: '9002', market: TW, name: B}\n"
        f"  kind: supplies\n  status: verified\n  origin: {origin}\n  {extra}"
    )
    path = tmp_path / "TW-9001.yaml"
    path.write_text(existing, encoding="utf-8")
    report = writer(tmp_path).apply([result()])
    assert report.protected == 1 and report.rows_written == 0
    assert path.read_text(encoding="utf-8") == existing


def test_other_rows_in_the_same_file_survive_and_none_is_deleted(tmp_path: Path) -> None:
    writer(tmp_path).apply([result(), result(customer=GAMMA)])
    writer(tmp_path).apply([result(evidence("https://wire.example.com/b", "wire.example.com"))])
    assert len(rows(tmp_path / "TW-9001.yaml")) == 2


def test_a_candidate_result_never_demotes_a_verified_row(tmp_path: Path) -> None:
    writer(tmp_path).apply([result()])
    writer(tmp_path).apply([result(status=Status.CANDIDATE)])
    [row] = rows(tmp_path / "TW-9001.yaml")
    assert row["status"] == "verified"
    writer(tmp_path).apply([result(customer=GAMMA, status=Status.CANDIDATE)])
    statuses = {r["customer"]["symbol"]: r["status"] for r in rows(tmp_path / "TW-9001.yaml")}  # type: ignore[index]
    assert statuses == {"9002": "verified", "ZZZA": "candidate"}


def test_a_row_found_in_another_file_is_updated_there(tmp_path: Path) -> None:
    writer(tmp_path).apply([result()])
    (tmp_path / "TW-9001.yaml").rename(tmp_path / "batch-001.yaml")
    writer(tmp_path).apply([result(evidence("https://wire.example.com/b", "wire.example.com"))])
    assert not (tmp_path / "TW-9001.yaml").exists()
    assert len(rows(tmp_path / "batch-001.yaml")[0]["evidence"]) == 2  # type: ignore[arg-type]


def test_dry_run_writes_nothing_but_reports(tmp_path: Path) -> None:
    report = writer(tmp_path, dry_run=True).apply([result()])
    assert report.dry_run and report.created == 1
    assert list(tmp_path.iterdir()) == []


def test_not_the_leader_refuses_to_write(tmp_path: Path) -> None:
    with pytest.raises(NotLeaderError):
        writer(tmp_path, leader=False).apply([result()])
    assert list(tmp_path.iterdir()) == []


def test_an_invalid_row_rolls_back_every_file(tmp_path: Path) -> None:
    writer(tmp_path).apply([result()])
    before = {p.name: p.read_bytes() for p in tmp_path.glob("*.yaml")}
    good_new = result(supplier=GAMMA.model_copy(update={"symbol": "ZZZB"}), customer=BETA)
    bad = result(customer=ALPHA)  # a company supplying itself fails the loader's own validation
    with pytest.raises(WriterError):
        writer(tmp_path).apply([good_new, bad])
    assert {p.name: p.read_bytes() for p in tmp_path.glob("*.yaml")} == before
    assert not list(tmp_path.glob(".*tmp*")) and not (tmp_path / LOCK_NAME).exists()


def test_a_failure_while_swapping_restores_files_already_swapped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer(tmp_path).apply([result()])
    before = (tmp_path / "TW-9001.yaml").read_bytes()
    other = GAMMA.model_copy(update={"symbol": "ZZZB", "market": "US"})
    original_replace = Path.replace
    calls = {"n": 0}

    def flaky(self: Path, target: Path) -> Path:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", flaky)
    with pytest.raises(WriterError):
        writer(tmp_path).apply(
            [
                result(evidence("https://wire.example.com/b", "wire.example.com")),
                result(supplier=other, customer=BETA),
            ]
        )
    monkeypatch.setattr(Path, "replace", original_replace)
    assert (tmp_path / "TW-9001.yaml").read_bytes() == before
    assert not (tmp_path / "US-ZZZB.yaml").exists()


def test_an_unreadable_existing_file_stops_the_write(tmp_path: Path) -> None:
    (tmp_path / "bad.yaml").write_text("not: [a list", encoding="utf-8")
    with pytest.raises(WriterError):
        writer(tmp_path).apply([result()])


def test_a_stale_lock_is_broken_and_a_live_lock_blocks(tmp_path: Path) -> None:
    lock = tmp_path / LOCK_NAME
    lock.write_text("1")
    quick = RelationWriter(
        tmp_path, is_leader=lambda: True, today=TODAY, trust_table=TRUST, lock_timeout=0.2
    )
    with pytest.raises(WriterError):
        quick.apply([result()])
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert quick.apply([result()]).created == 1
