from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.domain.models import Grade, Origin, Status
from analytics.supply_chain.refdata_io import loader
from analytics.supply_chain.refdata_io.loader import (
    RelationFileError,
    load_reference_relations,
    parse_relation_files,
)
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore

TODAY = date(2026, 10, 17)

RESEARCH = """
- supplier: {symbol: "9001", market: TW, name: Alpha Parts}
  customer: {symbol: "9002", market: TW, name: Beta Devices}
  origin: research
  product: widgets
  as_of: 2026-03-01
  evidence:
    - {url: "https://news.example/a/1", publisher: Pub A, excerpt: "synthetic", checker: human}
    - {url: "https://news.example/b/2", publisher: Pub B, published_on: 2026-02-01}
- supplier: {symbol: ZZZA, market: US, name: Gamma Corp}
  customer: {symbol: "9002", market: TW, name: Beta Devices}
  origin: research
  evidence:
    - {url: "https://weak.example/x", publisher: Only One}
"""

SEEDED = """
- supplier: {symbol: "9001", market: TW, name: Alpha Parts}
  customer: {symbol: ZZZA, market: US, name: Gamma Corp}
  origin: seeded
  grade: medium
"""

TRUST = "news.example: secondary\n"


def _write(directory: Path, name: str, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(text, encoding="utf-8")


@pytest.fixture
def reference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(loader, "reference_path", tmp_path.joinpath)
    return tmp_path


def test_parse_computes_trust_grade_and_status(reference: Path) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    _write(rel_dir, "a.yaml", RESEARCH)
    _write(rel_dir, "b.yaml", SEEDED)
    _write(reference / "supply_chain", "publisher_trust.yaml", TRUST)

    parsed = parse_relation_files(rel_dir, loader.load_trust_table(), TODAY)
    by_pair = {(r.supplier.symbol, r.customer.symbol): r for r in parsed.relations}

    two_sources = by_pair[("9001", "9002")]
    assert (two_sources.grade, two_sources.status) == (Grade.HIGH, Status.VERIFIED)
    weak = by_pair[("ZZZA", "9002")]  # one secondary publisher -> low
    assert weak.grade is Grade.LOW
    seeded = by_pair[("9001", "ZZZA")]
    assert (seeded.grade, seeded.status, seeded.origin) == (
        Grade.MEDIUM,
        Status.VERIFIED,
        Origin.SEEDED,
    )


def test_unknown_domains_count_as_tertiary_and_publish_at_low_grade(reference: Path) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    _write(rel_dir, "a.yaml", RESEARCH)  # no trust override: unknown domains are tertiary
    parsed = parse_relation_files(rel_dir, {}, TODAY)
    pairs = {(r.supplier.symbol, r.customer.symbol): r for r in parsed.relations}
    assert pairs[("9001", "9002")].grade is Grade.MEDIUM  # two tertiary publishers
    assert (pairs[("ZZZA", "9002")].grade, pairs[("ZZZA", "9002")].status) == (
        Grade.LOW,
        Status.VERIFIED,
    )


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        (
            "- supplier: {symbol: '1', market: JP, name: x}\n"
            "  customer: {symbol: '2', market: TW}\n"
            "  origin: research\n",
            "bad.yaml row[0]",
        ),
        (
            "- {supplier: {symbol: '1', market: TW}, customer: {symbol: '2', market: TW}, "
            "origin: research, grade: high}\n",
            "only seeded rows",
        ),
        (
            "- {supplier: {symbol: '1', market: TW}, customer: {symbol: '2', market: TW}, "
            "origin: seeded}\n",
            "must state its grade",
        ),
        ("not: a list\n", "expected a YAML list"),
    ],
)
def test_bad_rows_name_file_and_row(reference: Path, text: str, needle: str) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    _write(rel_dir, "bad.yaml", text)
    with pytest.raises(RelationFileError, match=needle.replace("[", r"\[").replace("]", r"\]")):
        parse_relation_files(rel_dir, {}, TODAY)


async def test_load_is_idempotent_and_file_is_truth(
    reference: Path, session_factory: async_sessionmaker
) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    _write(rel_dir, "a.yaml", RESEARCH)
    _write(rel_dir, "b.yaml", SEEDED)
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        first = await load_reference_relations(store, today=TODAY)
        again = await load_reference_relations(store, today=TODAY)
        assert (first.rows, first.created) == (3, 3)
        assert (again.created, again.updated, again.deleted) == (0, 3, 0)

        (rel_dir / "b.yaml").unlink()
        shrunk = await load_reference_relations(store, today=TODAY)
        assert shrunk.deleted == 1
        assert (await store.counts()).relations == 2


async def test_duplicate_keys_keep_the_first(
    reference: Path, session_factory: async_sessionmaker
) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    _write(rel_dir, "a.yaml", RESEARCH + RESEARCH)
    async with session_factory() as session:
        result = await load_reference_relations(SqlAlchemySupplyChainStore(session), today=TODAY)
    assert (result.rows, result.duplicates) == (2, 2)


async def test_missing_directory_is_a_noop_and_keeps_existing_rows(
    reference: Path, session_factory: async_sessionmaker
) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    _write(rel_dir, "a.yaml", RESEARCH)
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await load_reference_relations(store, today=TODAY)
        (rel_dir / "a.yaml").unlink()
        result = await load_reference_relations(store, today=TODAY)
        assert result.skipped is True
        assert (await store.counts()).relations == 2


async def test_bad_row_leaves_the_database_untouched(
    reference: Path, session_factory: async_sessionmaker
) -> None:
    rel_dir = reference / "supply_chain" / "relations"
    _write(rel_dir, "a.yaml", RESEARCH)
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await load_reference_relations(store, today=TODAY)
        _write(rel_dir, "z.yaml", "- {oops: 1}\n")
        with pytest.raises(RelationFileError):
            await load_reference_relations(store, today=TODAY)
        assert (await store.counts()).relations == 2
