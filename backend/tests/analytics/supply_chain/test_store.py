from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.domain.models import Grade, Kind, Origin, Status, Trust
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from tests.analytics.supply_chain.builders import ALPHA, BETA, GAMMA, evidence, relation


async def test_upsert_is_idempotent_and_replaces_evidence(
    session_factory: async_sessionmaker,
) -> None:
    first = relation(evidence_rows=(evidence(Trust.SECONDARY, "A"), evidence(Trust.TERTIARY, "B")))
    changed = relation(grade=Grade.LOW, evidence_rows=(evidence(Trust.PRIMARY, "C"),))
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        assert await store.upsert_relation(first) is True
        assert await store.upsert_relation(first) is False
        assert await store.upsert_relation(changed) is False
        await session.commit()
        counts = await store.counts()
        rows = await store.get_relations_for_symbol("9001")
    assert (counts.relations, counts.evidence) == (1, 1)
    assert rows[0].grade is Grade.LOW
    assert [e.publisher for e in rows[0].evidence] == ["C"]


async def test_product_distinguishes_relations(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.upsert_relation(relation(product="x"))
        await store.upsert_relation(relation(product="y"))
        await store.upsert_relation(relation(product=None))
        await store.upsert_relation(relation(product=None))
        assert (await store.counts()).relations == 3


async def test_delete_missing_removes_unlisted_rows_and_evidence(
    session_factory: async_sessionmaker,
) -> None:
    keep = relation(evidence_rows=(evidence(),))
    drop = relation(BETA, GAMMA, evidence_rows=(evidence(),))
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.upsert_relation(keep)
        await store.upsert_relation(drop)
        deleted = await store.delete_missing(origin_scope=list(Origin), keep_keys=[keep.key])
        await session.commit()
        counts = await store.counts()
    assert deleted == 1
    assert (counts.relations, counts.evidence) == (1, 1)


async def test_delete_missing_respects_origin_scope(session_factory: async_sessionmaker) -> None:
    manual = relation(origin=Origin.MANUAL)
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.upsert_relation(manual)
        assert await store.delete_missing(origin_scope=[Origin.RESEARCH], keep_keys=[]) == 0
        assert (await store.counts()).relations == 1


async def test_reads_hide_non_public_and_match_market(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.upsert_relation(relation(ALPHA, BETA))
        await store.upsert_relation(relation(ALPHA, GAMMA, status=Status.CANDIDATE))
        await store.upsert_relation(
            relation(GAMMA, BETA, kind=Kind.COMPETES, as_of=date(2020, 1, 1))
        )
        public = await store.get_relations_for_symbol("9001")
        everything = await store.get_relations_for_symbol("9001", include_non_public=True)
        wrong_market = await store.get_relations_for_symbol("9001", "US")
        as_customer = await store.get_relations_for_symbol("9002", "TW")
    assert [r.customer.symbol for r in public] == ["9002"]
    assert len(everything) == 2
    assert wrong_market == []
    assert [r.supplier.symbol for r in as_customer] == ["9001"]  # stale 2020 row hidden


async def test_counts_group_by(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.upsert_relation(relation(ALPHA, BETA))
        await store.upsert_relation(relation(GAMMA, BETA, grade=Grade.LOW))
        counts = await store.counts()
    assert counts.by_market == {"TW": 1, "US": 1}
    assert counts.by_grade == {"high": 1, "low": 1}
    assert counts.by_kind == {"supplies": 2}
