"""Read the private relation files and rebuild the index tables from them.

All files are parsed and validated BEFORE anything is written, so one bad row fails the load
without touching the database. A missing/empty directory is a no-op (one warning): a misplaced
mount must not wipe the index.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import cast

import structlog
import yaml
from pydantic import ValidationError

from analytics.supply_chain.domain.models import (
    Evidence,
    Grade,
    Origin,
    Relation,
    RelationKey,
    Trust,
)
from analytics.supply_chain.domain.rules import (
    grade_from_evidence,
    is_stale,
    resolve_status,
    trust_for_url,
)
from analytics.supply_chain.refdata_io.schema import RelationFileRow
from analytics.supply_chain.store.protocol import SupplyChainStore
from config.private_data import reference_path

logger = structlog.get_logger(__name__)

RELATIONS_SUBDIR = ("supply_chain", "relations")
TRUST_FILE = ("supply_chain", "publisher_trust.yaml")


class RelationFileError(ValueError):
    """A relation file is unreadable or has an invalid row; the message names file and row."""


@dataclass(frozen=True)
class ParsedRelations:
    relations: list[Relation]
    files: int
    duplicates: int


@dataclass(frozen=True)
class LoadResult:
    files: int
    rows: int
    created: int
    updated: int
    deleted: int
    duplicates: int
    skipped: bool


def load_trust_table(path: Path | None = None) -> dict[str, Trust]:
    """The optional operator override of domain -> trust (`domain: primary|secondary|...`)."""
    target = path or reference_path(*TRUST_FILE)
    if not target.is_file():
        return {}
    try:
        raw: object = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RelationFileError(f"{target.name}: cannot read ({exc})") from exc
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RelationFileError(f"{target.name}: expected a mapping of domain to trust")
    table: dict[str, Trust] = {}
    for domain, value in cast("dict[object, object]", raw).items():
        try:
            table[str(domain).lower()] = Trust(str(value))
        except ValueError as exc:
            raise RelationFileError(f"{target.name}: bad trust for {domain!r}") from exc
    return table


def build_relation(row: RelationFileRow, trust_table: Mapping[str, Trust], today: date) -> Relation:
    """Compute trust, grade, staleness and status for one validated file row."""
    evidence = tuple(
        Evidence(
            url=e.url,
            publisher=e.publisher,
            published_on=e.published_on,
            excerpt=e.excerpt,
            trust=trust_for_url(e.url, trust_table),
            checker=e.checker,
        )
        for e in row.evidence
    )
    seeded = row.origin is Origin.SEEDED
    earned = grade_from_evidence(evidence)
    stale = is_stale(row.as_of, row.last_verified_at, today)
    status = resolve_status(
        declared=row.status, origin=row.origin, evidence_grade=earned, stale=stale
    )
    grade = row.grade if seeded and row.grade is not None else (earned or Grade.LOW)
    verified_at = (
        datetime.combine(row.last_verified_at, time.min, tzinfo=UTC)
        if row.last_verified_at
        else None
    )
    return Relation(
        supplier=row.supplier,
        customer=row.customer,
        kind=row.kind,
        product=row.product,
        status=status,
        grade=grade,
        origin=row.origin,
        revenue_pct=row.revenue_pct,
        as_of=row.as_of,
        last_verified_at=verified_at,
        evidence=evidence,
    )


def _read_rows(path: Path) -> list[object]:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RelationFileError(f"{path.name}: cannot read ({exc})") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise RelationFileError(f"{path.name}: expected a YAML list of rows")
    return list(cast("list[object]", raw))


def parse_relation_files(
    directory: Path, trust_table: Mapping[str, Trust], today: date
) -> ParsedRelations:
    relations: dict[RelationKey, Relation] = {}
    duplicates = 0
    files = sorted(directory.glob("*.yaml"))
    for path in files:
        for index, raw in enumerate(_read_rows(path)):
            try:
                row = RelationFileRow.model_validate(raw)
                relation = build_relation(row, trust_table, today)
            except ValidationError as exc:
                problems = "; ".join(
                    f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
                )
                raise RelationFileError(f"{path.name} row[{index}]: {problems}") from exc
            if relation.key in relations:
                duplicates += 1
                continue
            relations[relation.key] = relation
    return ParsedRelations(list(relations.values()), len(files), duplicates)


async def load_reference_relations(
    store: SupplyChainStore, *, today: date, directory: Path | None = None
) -> LoadResult:
    """Upsert every file row and delete index rows the files no longer contain. Idempotent."""
    source = directory or reference_path(*RELATIONS_SUBDIR)
    if not source.is_dir() or not any(source.glob("*.yaml")):
        logger.warning("sc_relations_dir_missing", path=str(source))
        return LoadResult(0, 0, 0, 0, 0, 0, skipped=True)
    parsed = parse_relation_files(source, load_trust_table(), today)
    created = 0
    for relation in parsed.relations:
        created += int(await store.upsert_relation(relation))
    deleted = await store.delete_missing(
        origin_scope=list(Origin), keep_keys=[r.key for r in parsed.relations]
    )
    total = len(parsed.relations)
    return LoadResult(
        files=parsed.files,
        rows=total,
        created=created,
        updated=total - created,
        deleted=deleted,
        duplicates=parsed.duplicates,
        skipped=False,
    )


__all__ = [
    "LoadResult",
    "ParsedRelations",
    "RelationFileError",
    "build_relation",
    "load_reference_relations",
    "load_trust_table",
    "parse_relation_files",
]
