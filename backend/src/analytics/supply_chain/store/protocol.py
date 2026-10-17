"""The persistence seam. Implementations never commit: the caller owns the transaction."""

from __future__ import annotations

from collections.abc import Collection
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.domain.models import (
    MarketCode,
    Origin,
    Relation,
    RelationKey,
)


class ScCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    relations: int
    evidence: int
    by_status: dict[str, int]
    by_kind: dict[str, int]
    by_market: dict[str, int]
    by_grade: dict[str, int]


@runtime_checkable
class SupplyChainStore(Protocol):
    async def upsert_relation(self, relation: Relation) -> bool:
        """Insert or update by natural key, replacing the evidence set. True when created."""
        ...

    async def delete_missing(
        self, *, origin_scope: Collection[Origin], keep_keys: Collection[RelationKey]
    ) -> int:
        """Delete relations of the given origins whose key is not in `keep_keys`."""
        ...

    async def get_relations_for_symbol(
        self,
        symbol: str,
        market: MarketCode | None = None,
        include_non_public: bool = False,
    ) -> list[Relation]:
        """Relations where the company is supplier or customer. Public = verified, not stale."""
        ...

    async def counts(self) -> ScCounts: ...
