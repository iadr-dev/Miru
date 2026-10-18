"""The persistence seam. Implementations never commit: the caller owns the transaction."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.domain.models import (
    MarketCode,
    Origin,
    Relation,
    RelationKey,
)
from analytics.supply_chain.positions.models import ChainPosition


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

    async def list_supply_pairs(self) -> list[tuple[str, str]]:
        """Distinct `(supplier, customer)` symbols of the verified, non-stale `supplies`
        relations whose two ends are both Taiwan companies - the positions graph's edges."""
        ...

    async def replace_positions(
        self, positions: Sequence[ChainPosition], *, computed_at: datetime
    ) -> int:
        """Replace every stored position with `positions` (derived data, rewritten whole) in the
        caller's transaction. Returns the number written."""
        ...

    async def get_positions_for_symbol(
        self, symbol: str, market: MarketCode = "TW"
    ) -> list[ChainPosition]:
        """The stored positions of one company, primary chain first."""
        ...

    async def counts(self) -> ScCounts: ...
