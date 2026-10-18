"""Read the private entity catalog and mention files and rebuild the entity tables from them.

All rows are validated BEFORE anything is written, so one bad row fails the load without touching
the database. A mention whose entity is not in the catalog, or whose company symbol is not a
valid Taiwan code, is dropped (and counted). A missing catalog is a no-op (one warning): a
misplaced mount must not wipe the index.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import structlog
import yaml
from pydantic import ValidationError

from analytics.supply_chain.domain.concepts import Entity, EntityMention, market_of_symbol
from analytics.supply_chain.entities_io.schema import CatalogFileRow, MentionFileRow
from analytics.supply_chain.store.concept_protocol import ConceptStore
from config.private_data import reference_path

logger = structlog.get_logger(__name__)

ENTITIES_SUBDIR = ("entities",)
CATALOG_FILE = "catalog.yaml"
MENTIONS_FILE = "mentions.jsonl"


class EntityFileError(ValueError):
    """An entity file is unreadable or has an invalid row; the message names file and row."""


@dataclass(frozen=True)
class ParsedEntities:
    entities: list[Entity]
    mentions: list[EntityMention]
    dropped: int
    duplicates: int


@dataclass(frozen=True)
class EntitiesLoadResult:
    entities: int
    mentions: int
    dropped: int
    duplicates: int
    created_mentions: int
    deleted_entities: int
    deleted_mentions: int
    skipped: bool


def _problems(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def parse_catalog(path: Path) -> list[Entity]:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EntityFileError(f"{path.name}: cannot read ({exc})") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise EntityFileError(f"{path.name}: expected a YAML list of entities")
    entities: dict[str, Entity] = {}
    for index, item in enumerate(cast("list[object]", raw)):
        try:
            row = CatalogFileRow.model_validate(item)
            market = None
            if row.symbol is not None:
                market = market_of_symbol(row.symbol)
                if market is None:
                    raise EntityFileError(
                        f"{path.name} row[{index}]: symbol is neither a Taiwan code nor a ticker"
                    )
            entity = Entity(
                id=row.id,
                type=row.type,
                symbol=row.symbol,
                market=market,
                aliases=row.aliases,
                mentions=row.mentions,
                reports=row.reports,
            )
        except ValidationError as exc:
            raise EntityFileError(f"{path.name} row[{index}]: {_problems(exc)}") from exc
        if entity.id in entities:
            raise EntityFileError(f"{path.name} row[{index}]: duplicate entity id")
        entities[entity.id] = entity
    return list(entities.values())


def parse_mentions(path: Path, known: set[str]) -> tuple[list[EntityMention], int, int]:
    """(mentions, dropped, duplicates). A malformed line raises; an unknown entity or symbol is
    dropped; a repeated key keeps its first row."""
    mentions: dict[tuple[str, str, str, str], EntityMention] = {}
    dropped = 0
    duplicates = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EntityFileError(f"{path.name}: cannot read ({exc})") from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = MentionFileRow.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError) as exc:
            detail = _problems(exc) if isinstance(exc, ValidationError) else "invalid JSON"
            raise EntityFileError(f"{path.name} line {number}: {detail}") from exc
        if row.entity not in known or market_of_symbol(row.symbol) != "TW":
            dropped += 1
            continue
        mention = EntityMention(
            symbol=row.symbol,
            entity_id=row.entity,
            section=row.section,
            role=row.role,
            count=row.count,
        )
        if mention.key in mentions:
            duplicates += 1
            continue
        mentions[mention.key] = mention
    return list(mentions.values()), dropped, duplicates


def parse_entity_files(directory: Path) -> ParsedEntities:
    entities = parse_catalog(directory / CATALOG_FILE)
    mentions_path = directory / MENTIONS_FILE
    if not mentions_path.is_file():
        logger.warning("sc_mentions_file_missing", path=str(mentions_path))
        return ParsedEntities(entities, [], 0, 0)
    mentions, dropped, duplicates = parse_mentions(mentions_path, {e.id for e in entities})
    return ParsedEntities(entities, mentions, dropped, duplicates)


async def load_entities(
    store: ConceptStore, *, directory: Path | None = None
) -> EntitiesLoadResult:
    """Make the entity tables match the files exactly. Idempotent."""
    source = directory or reference_path(*ENTITIES_SUBDIR)
    if not (source / CATALOG_FILE).is_file():
        logger.warning("sc_entities_dir_missing", path=str(source))
        return EntitiesLoadResult(0, 0, 0, 0, 0, 0, 0, skipped=True)
    parsed = parse_entity_files(source)
    if not parsed.entities:
        logger.warning("sc_entities_catalog_empty", path=str(source))
        return EntitiesLoadResult(0, 0, 0, 0, 0, 0, 0, skipped=True)
    result = await store.replace_entities(parsed.entities, parsed.mentions)
    return EntitiesLoadResult(
        entities=result.entities,
        mentions=result.mentions,
        dropped=parsed.dropped,
        duplicates=parsed.duplicates,
        created_mentions=result.created_mentions,
        deleted_entities=result.deleted_entities,
        deleted_mentions=result.deleted_mentions,
        skipped=False,
    )


__all__ = [
    "EntitiesLoadResult",
    "EntityFileError",
    "ParsedEntities",
    "load_entities",
    "parse_catalog",
    "parse_entity_files",
    "parse_mentions",
]
