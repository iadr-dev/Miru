"""The persistence seam for concept themes and entities. Implementations never commit."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from analytics.supply_chain.domain.concepts import (
    ConceptPosition,
    ConceptTheme,
    Entity,
    EntityMention,
)


class ConceptReplaceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    themes: int
    members: int
    created: int
    deleted: int


class EntityReplaceResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    entities: int
    mentions: int
    created_mentions: int
    deleted_entities: int
    deleted_mentions: int


class ConceptInfo(BaseModel):
    """What a concept theme adds to the generic theme row."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    description: str = ""
    related_keys: tuple[str, ...] = ()


class ConceptCounts(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    concept_themes: int
    concept_members: int
    entities: int
    entity_mentions: int
    entities_by_type: dict[str, int]


class ConceptCollisionError(ValueError):
    """A concept theme key is already used by an industry theme."""


@runtime_checkable
class ConceptStore(Protocol):
    async def replace_concept_themes(self, themes: Sequence[ConceptTheme]) -> ConceptReplaceResult:
        """Make the concept tables (and their mirror in `themes`/`theme_memberships`) match
        `themes` exactly: upsert, replace each theme's members, delete themes no longer listed."""
        ...

    async def replace_entities(
        self, entities: Sequence[Entity], mentions: Sequence[EntityMention]
    ) -> EntityReplaceResult:
        """Make the entity tables match the arguments exactly. Mentions of an unlisted entity
        must already have been dropped by the caller."""
        ...

    async def concept_info(self, keys: Collection[str] | None = None) -> dict[str, ConceptInfo]: ...

    async def concept_positions(self, theme_key: str) -> dict[tuple[str, str], ConceptPosition]:
        """`(symbol, market)` -> position for one concept theme (empty when it is not one)."""
        ...

    async def entity_totals(self, symbol: str) -> dict[str, int]:
        """Entity id -> total mention count across every section and role for one company."""
        ...

    async def mentions_of_entities(self, entity_ids: Collection[str]) -> list[tuple[str, str, int]]:
        """`(symbol, entity_id, total count)` for every company mentioning any of the entities."""
        ...

    async def distinct_entity_counts(self, symbols: Collection[str]) -> dict[str, int]: ...

    async def get_entities(self, ids: Collection[str]) -> dict[str, Entity]: ...

    async def entities_by_symbol(self, symbols: Collection[str]) -> dict[tuple[str, str], Entity]:
        """Company entities keyed by `(symbol, market)`."""
        ...

    async def counts(self) -> ConceptCounts: ...


__all__ = [
    "ConceptCollisionError",
    "ConceptCounts",
    "ConceptInfo",
    "ConceptReplaceResult",
    "ConceptStore",
    "EntityReplaceResult",
]
