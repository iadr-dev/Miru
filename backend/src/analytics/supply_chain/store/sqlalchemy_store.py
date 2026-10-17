"""`SupplyChainStore` over one `AsyncSession`. Never commits; the caller owns the transaction."""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute
from sqlalchemy.sql.elements import ColumnElement

from analytics.supply_chain.domain.models import (
    Checker,
    Endpoint,
    Evidence,
    Grade,
    Kind,
    MarketCode,
    Origin,
    Relation,
    RelationKey,
    Status,
    Trust,
)
from analytics.supply_chain.domain.rules import is_stale
from analytics.supply_chain.store.protocol import ScCounts
from db.models import ScEvidenceRow, ScRelationRow

_CHUNK = 500


def _key_of(row: ScRelationRow) -> RelationKey:
    return RelationKey(
        supplier_symbol=row.supplier_symbol,
        supplier_market=cast("MarketCode", row.supplier_market),
        customer_symbol=row.customer_symbol,
        customer_market=cast("MarketCode", row.customer_market),
        kind=Kind(row.kind),
        product_key=row.product_key,
    )


def _evidence_of(row: ScEvidenceRow) -> Evidence:
    return Evidence(
        url=row.url,
        publisher=row.publisher,
        published_on=row.published_on,
        excerpt=row.excerpt,
        trust=Trust(row.trust),
        checker=Checker(row.checker),
        supports=row.supports,
    )


def _relation_of(row: ScRelationRow, evidence: Sequence[ScEvidenceRow]) -> Relation:
    return Relation(
        supplier=Endpoint(
            symbol=row.supplier_symbol,
            market=cast("MarketCode", row.supplier_market),
            name=row.supplier_name,
        ),
        customer=Endpoint(
            symbol=row.customer_symbol,
            market=cast("MarketCode", row.customer_market),
            name=row.customer_name,
        ),
        kind=Kind(row.kind),
        product=row.product,
        status=Status(row.status),
        grade=Grade(row.grade),
        origin=Origin(row.origin),
        revenue_pct=row.revenue_pct,
        as_of=row.as_of,
        last_verified_at=row.last_verified_at,
        evidence=tuple(_evidence_of(e) for e in evidence),
    )


def _key_clause(key: RelationKey) -> ColumnElement[bool]:
    return and_(
        ScRelationRow.supplier_symbol == key.supplier_symbol,
        ScRelationRow.supplier_market == key.supplier_market,
        ScRelationRow.customer_symbol == key.customer_symbol,
        ScRelationRow.customer_market == key.customer_market,
        ScRelationRow.kind == key.kind.value,
        ScRelationRow.product_key == key.product_key,
    )


class SqlAlchemySupplyChainStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_relation(self, relation: Relation) -> bool:
        now = datetime.now(UTC)
        row = await self._session.scalar(select(ScRelationRow).where(_key_clause(relation.key)))
        created = row is None
        if row is None:
            row = ScRelationRow(
                id=str(uuid.uuid4()),
                supplier_symbol=relation.supplier.symbol,
                supplier_market=relation.supplier.market,
                customer_symbol=relation.customer.symbol,
                customer_market=relation.customer.market,
                kind=relation.kind.value,
                product_key=relation.product or "",
                created_at=now,
                status=relation.status.value,
                grade=relation.grade.value,
                origin=relation.origin.value,
            )
            self._session.add(row)
        row.product = relation.product
        row.status = relation.status.value
        row.grade = relation.grade.value
        row.origin = relation.origin.value
        row.revenue_pct = relation.revenue_pct
        row.as_of = relation.as_of
        row.last_verified_at = relation.last_verified_at
        row.supplier_name = relation.supplier.name
        row.customer_name = relation.customer.name
        row.updated_at = now
        await self._session.flush()
        await self._session.execute(
            delete(ScEvidenceRow).where(ScEvidenceRow.relation_id == row.id)
        )
        self._session.add_all(
            ScEvidenceRow(
                id=str(uuid.uuid4()),
                relation_id=row.id,
                url=e.url,
                publisher=e.publisher,
                trust=e.trust.value,
                published_on=e.published_on,
                excerpt=e.excerpt,
                checker=e.checker.value,
                supports=e.supports,
            )
            for e in relation.evidence
        )
        await self._session.flush()
        return created

    async def delete_missing(
        self, *, origin_scope: Collection[Origin], keep_keys: Collection[RelationKey]
    ) -> int:
        keep = set(keep_keys)
        origins = [o.value for o in origin_scope]
        rows = (
            await self._session.execute(
                select(ScRelationRow).where(ScRelationRow.origin.in_(origins))
            )
        ).scalars()
        doomed = [row.id for row in rows if _key_of(row) not in keep]
        for start in range(0, len(doomed), _CHUNK):
            chunk = doomed[start : start + _CHUNK]
            await self._session.execute(
                delete(ScEvidenceRow).where(ScEvidenceRow.relation_id.in_(chunk))
            )
            await self._session.execute(delete(ScRelationRow).where(ScRelationRow.id.in_(chunk)))
        return len(doomed)

    async def get_relations_for_symbol(
        self,
        symbol: str,
        market: MarketCode | None = None,
        include_non_public: bool = False,
    ) -> list[Relation]:
        as_supplier = ScRelationRow.supplier_symbol == symbol
        as_customer = ScRelationRow.customer_symbol == symbol
        if market is not None:
            as_supplier = and_(as_supplier, ScRelationRow.supplier_market == market)
            as_customer = and_(as_customer, ScRelationRow.customer_market == market)
        stmt = select(ScRelationRow).where(or_(as_supplier, as_customer))
        if not include_non_public:
            stmt = stmt.where(ScRelationRow.status == Status.VERIFIED.value)
        stmt = stmt.order_by(
            ScRelationRow.supplier_symbol, ScRelationRow.customer_symbol, ScRelationRow.product_key
        )
        rows = list((await self._session.scalars(stmt)).all())
        if not include_non_public:
            today = datetime.now(UTC).date()
            rows = [r for r in rows if not _row_is_stale(r, today)]
        by_relation = await self._evidence_for([r.id for r in rows])
        return [_relation_of(r, by_relation.get(r.id, ())) for r in rows]

    async def _evidence_for(self, ids: list[str]) -> dict[str, list[ScEvidenceRow]]:
        out: dict[str, list[ScEvidenceRow]] = {}
        for start in range(0, len(ids), _CHUNK):
            stmt = (
                select(ScEvidenceRow)
                .where(ScEvidenceRow.relation_id.in_(ids[start : start + _CHUNK]))
                .order_by(ScEvidenceRow.publisher, ScEvidenceRow.url)
            )
            for ev in (await self._session.scalars(stmt)).all():
                out.setdefault(ev.relation_id, []).append(ev)
        return out

    async def counts(self) -> ScCounts:
        return ScCounts(
            relations=await self._total(ScRelationRow),
            evidence=await self._total(ScEvidenceRow),
            by_status=await self._grouped(ScRelationRow.status),
            by_kind=await self._grouped(ScRelationRow.kind),
            by_market=await self._grouped(ScRelationRow.supplier_market),
            by_grade=await self._grouped(ScRelationRow.grade),
        )

    async def _total(self, model: type[ScRelationRow] | type[ScEvidenceRow]) -> int:
        return int(await self._session.scalar(select(func.count()).select_from(model)) or 0)

    async def _grouped(self, column: InstrumentedAttribute[str]) -> dict[str, int]:
        result = await self._session.execute(select(column, func.count()).group_by(column))
        return {str(value): int(n) for value, n in result.all()}


def _row_is_stale(row: ScRelationRow, today: date) -> bool:
    verified = row.last_verified_at.date() if row.last_verified_at else None
    return is_stale(row.as_of, verified, today)
