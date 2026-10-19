"""File layer of theme mining: read the private entity and theme files, write the candidates file.

`<reference>/themes/theme_candidates.jsonl` is private and never published. The previous file is
rotated to `theme_candidates.prev.jsonl` so the next run can report a trend. Each write is a temp
file in the same directory, fsync'd, then `os.replace`d, so a reader sees the old or the new file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog
from pydantic import ValidationError

from analytics.supply_chain.entities_io.loader import (
    CATALOG_FILE,
    MENTIONS_FILE,
    EntityFileError,
    parse_entity_files,
)
from analytics.supply_chain.theme_discovery.mining import (
    DEFAULT_LIMIT,
    DEFAULT_MIN_COMPANIES,
    mine_candidates,
)
from analytics.supply_chain.theme_discovery.models import PreviousCandidate, ThemeCandidate
from analytics.supply_chain.themes_io.loader import (
    CONCEPTS_SUBDIR,
    ConceptFileError,
    parse_concept_files,
)

logger = structlog.get_logger(__name__)

ENTITIES_DIR = "entities"
CANDIDATES_FILE = ("themes", "theme_candidates.jsonl")
PREVIOUS_FILE = ("themes", "theme_candidates.prev.jsonl")


class ThemeMiningError(ValueError):
    """An input file is unusable; nothing was written."""


@dataclass(frozen=True)
class MiningResult:
    candidates: list[ThemeCandidate]
    written: bool
    path: Path


def missing_inputs(root: Path) -> str | None:
    """Why mining cannot run, or None. Absent files are a skip, not an error."""
    if not (root / ENTITIES_DIR / CATALOG_FILE).is_file():
        return "entity catalog not found"
    if not (root / ENTITIES_DIR / MENTIONS_FILE).is_file():
        return "entity mentions not found"
    return None


def read_previous(path: Path) -> dict[str, PreviousCandidate]:
    if not path.is_file():
        return {}
    out: dict[str, PreviousCandidate] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = PreviousCandidate.model_validate(json.loads(line))
        except (json.JSONDecodeError, ValidationError):
            logger.warning("theme_candidates_prev_line_ignored")
            continue
        out[row.candidate_id] = row
    return out


def _dump(candidates: list[ThemeCandidate]) -> str:
    return "".join(
        json.dumps(c.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
        for c in candidates
    )


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def run_theme_mining(
    root: Path,
    *,
    today: date,
    min_companies: int = DEFAULT_MIN_COMPANIES,
    limit: int = DEFAULT_LIMIT,
    dry_run: bool = False,
) -> MiningResult:
    """Mine and (unless dry) write. Raises `ThemeMiningError` on an invalid input file."""
    target = root.joinpath(*CANDIDATES_FILE)
    try:
        parsed = parse_entity_files(root / ENTITIES_DIR)
        concepts = root.joinpath(*CONCEPTS_SUBDIR)
        themes = parse_concept_files(concepts)[0] if concepts.is_dir() else []
    except (EntityFileError, ConceptFileError) as exc:
        raise ThemeMiningError(str(exc)) from exc
    previous = read_previous(target)
    candidates = mine_candidates(
        parsed.entities,
        parsed.mentions,
        themes,
        min_companies=min_companies,
        limit=limit,
        previous=previous,
        today=today,
    )
    if dry_run:
        return MiningResult(candidates, written=False, path=target)
    if target.is_file():
        write_atomic(root.joinpath(*PREVIOUS_FILE), target.read_text(encoding="utf-8"))
    write_atomic(target, _dump(candidates))
    return MiningResult(candidates, written=True, path=target)


def format_table(candidates: list[ThemeCandidate]) -> str:
    """Compact table: rank, label (a technology/material/application/product name, never a
    company), type, company count, trend and up to five stock symbols. No company name."""
    lines = [f"candidates={len(candidates)}", "rank label type companies trend symbols"]
    for rank, c in enumerate(candidates, start=1):
        symbols = ",".join(c.top_companies[:5])
        lines.append(f"{rank} {c.label} {c.type} {c.company_count} {c.trend_text()} {symbols}")
    return "\n".join(lines) + "\n"


__all__ = [
    "CANDIDATES_FILE",
    "PREVIOUS_FILE",
    "MiningResult",
    "ThemeMiningError",
    "format_table",
    "missing_inputs",
    "read_previous",
    "run_theme_mining",
    "write_atomic",
]
