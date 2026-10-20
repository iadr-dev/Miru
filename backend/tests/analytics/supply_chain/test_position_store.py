"""`sc_positions` store: whole-table replace, the one-primary constraint, supply pairs."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from analytics.supply_chain.domain.models import Endpoint, Kind, Status
from analytics.supply_chain.positions.models import (
    ChainPosition,
    Method,
    Position,
    Reason,
    ReasonCode,
)
from analytics.supply_chain.store.protocol import SupplyChainStore
from analytics.supply_chain.store.sqlalchemy_store import SqlAlchemySupplyChainStore
from tests.analytics.supply_chain.builders import ALPHA, BETA, GAMMA, relation

NOW = datetime(2026, 10, 17, 6, 30, tzinfo=UTC)
LATER = datetime(2026, 10, 18, 6, 30, tzinfo=UTC)


def _pos(
    symbol: str, chain: str, *, primary: bool = False, position: Position = Position.MIDSTREAM
):
    return ChainPosition(
        symbol=symbol,
        chain_key=chain,
        chain_name=f"name-{chain}",
        position=position,
        is_primary=primary,
        method=Method.EVIDENCE,
        confidence=0.7,
        reasons=(Reason(code=ReasonCode.GRAPH_LAYERING, detail="d"),),
        inputs_hash="abc",
    )


def test_sqlalchemy_store_satisfies_the_protocol(session_factory: async_sessionmaker) -> None:
    assert issubclass(SqlAlchemySupplyChainStore, SupplyChainStore)


async def test_replace_writes_and_reads_back_primary_first(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        written = await store.replace_positions(
            [_pos("9001", "b"), _pos("9001", "a", primary=True), _pos("9002", "a", primary=True)],
            computed_at=NOW,
        )
        assert written == 3
        rows = await store.get_positions_for_symbol("9001")
    assert [(r.chain_key, r.is_primary) for r in rows] == [("a", True), ("b", False)]
    first = rows[0]
    assert first.computed_at is not None
    assert first.computed_at.replace(tzinfo=UTC) == NOW
    assert first.reasons == (Reason(code=ReasonCode.GRAPH_LAYERING, detail="d"),)
    assert (first.method, first.confidence, first.inputs_hash) == (Method.EVIDENCE, 0.7, "abc")


async def test_replace_is_a_full_idempotent_replace(session_factory: async_sessionmaker) -> None:
    batch = [_pos("9001", "a", primary=True), _pos("9002", "a", primary=True)]
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.replace_positions(batch, computed_at=NOW)
        await store.replace_positions(batch, computed_at=LATER)  # same batch again
        assert len(await store.get_positions_for_symbol("9001")) == 1
        await store.replace_positions([_pos("9003", "c", primary=True)], computed_at=LATER)
        assert await store.get_positions_for_symbol("9001") == []
        assert len(await store.get_positions_for_symbol("9003")) == 1
        assert await store.replace_positions([], computed_at=LATER) == 0
        assert await store.get_positions_for_symbol("9003") == []


async def test_database_allows_only_one_primary_per_company(
    session_factory: async_sessionmaker,
) -> None:
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        positions = [_pos("9001", "a", primary=True), _pos("9001", "b", primary=True)]
        with pytest.raises(IntegrityError):
            await store.replace_positions(positions, computed_at=NOW)


async def test_database_rejects_a_duplicate_chain_row(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        positions = [_pos("9001", "a"), _pos("9001", "a")]
        with pytest.raises(IntegrityError):
            await store.replace_positions(positions, computed_at=NOW)


async def test_supply_pairs_are_verified_fresh_supplies_between_taiwan_companies(
    session_factory: async_sessionmaker,
) -> None:
    delta = Endpoint(symbol="9003", market="TW", name="Delta Co")
    async with session_factory() as session:
        store = SqlAlchemySupplyChainStore(session)
        await store.upsert_relation(relation(ALPHA, BETA, product="x"))
        await store.upsert_relation(relation(ALPHA, BETA, product="y"))  # same pair, once
        await store.upsert_relation(relation(BETA, delta))
        await store.upsert_relation(relation(delta, ALPHA, status=Status.CANDIDATE))
        await store.upsert_relation(relation(GAMMA, ALPHA))  # US supplier
        await store.upsert_relation(relation(ALPHA, delta, kind=Kind.COMPETES))
        await store.upsert_relation(relation(delta, BETA, as_of=date(2019, 1, 1)))  # stale
        assert await store.list_supply_pairs() == [("9001", "9002"), ("9002", "9003")]
