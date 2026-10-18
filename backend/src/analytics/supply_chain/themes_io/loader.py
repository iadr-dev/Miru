"""Read the private concept-theme files and rebuild the concept tables (and their mirror in the
generic theme tables) from them.

Every file is parsed and validated BEFORE anything is written, so one bad row fails the load
without touching the database. A missing/empty directory is a no-op (one warning): a misplaced
mount must not wipe the index.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import structlog
import yaml
from pydantic import ValidationError

from analytics.supply_chain.domain.concepts import (
    ConceptMember,
    ConceptTheme,
    market_of_symbol,
)
from analytics.supply_chain.store.concept_protocol import ConceptStore
from analytics.supply_chain.themes_io.schema import ConceptThemeFile
from config.private_data import reference_path

logger = structlog.get_logger(__name__)

CONCEPTS_SUBDIR = ("themes", "concepts")


class ConceptFileError(ValueError):
    """A concept file is unreadable or has an invalid row; the message names file and row."""


@dataclass(frozen=True)
class ThemesLoadResult:
    files: int
    themes: int
    members: int
    created: int
    deleted: int
    skipped: bool


def _problems(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())


def build_theme(raw: ConceptThemeFile, file_name: str) -> ConceptTheme:
    members: list[ConceptMember] = []
    for index, row in enumerate(raw.members):
        market = market_of_symbol(row.symbol)
        if market is None:
            raise ConceptFileError(f"{file_name} members[{index}]: unrecognised symbol format")
        members.append(
            ConceptMember(
                symbol=row.symbol,
                market=market,
                name=row.name,
                position=row.position,
                origin=row.origin,
            )
        )
    try:
        return ConceptTheme(
            key=raw.key,
            name_zh=raw.name_zh,
            name_en=raw.name_en,
            description=raw.description,
            related=raw.related,
            members=tuple(members),
        )
    except ValidationError as exc:
        raise ConceptFileError(f"{file_name}: {_problems(exc)}") from exc


def parse_concept_files(directory: Path) -> tuple[list[ConceptTheme], int]:
    """(themes, file count). Raises `ConceptFileError` on the first bad file."""
    themes: dict[str, ConceptTheme] = {}
    files = sorted(directory.glob("*.yaml"))
    for path in files:
        try:
            raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ConceptFileError(f"{path.name}: cannot read ({exc})") from exc
        if not isinstance(raw, dict):
            raise ConceptFileError(f"{path.name}: expected one theme mapping")
        try:
            parsed = ConceptThemeFile.model_validate(cast("dict[str, object]", raw))
        except ValidationError as exc:
            raise ConceptFileError(f"{path.name}: {_problems(exc)}") from exc
        theme = build_theme(parsed, path.name)
        if theme.key in themes:
            raise ConceptFileError(f"{path.name}: duplicate theme key")
        themes[theme.key] = theme
    return list(themes.values()), len(files)


async def load_concept_themes(
    store: ConceptStore, *, directory: Path | None = None
) -> ThemesLoadResult:
    """Make the concept tables match the files exactly. Idempotent."""
    source = directory or reference_path(*CONCEPTS_SUBDIR)
    if not source.is_dir() or not any(source.glob("*.yaml")):
        logger.warning("sc_concept_dir_missing", path=str(source))
        return ThemesLoadResult(0, 0, 0, 0, 0, skipped=True)
    themes, files = parse_concept_files(source)
    result = await store.replace_concept_themes(themes)
    return ThemesLoadResult(
        files=files,
        themes=result.themes,
        members=result.members,
        created=result.created,
        deleted=result.deleted,
        skipped=False,
    )


__all__ = [
    "ConceptFileError",
    "ThemesLoadResult",
    "build_theme",
    "load_concept_themes",
    "parse_concept_files",
]
