"""File layer of theme mining: read the private entity and theme files, write the candidates file.

`<reference>/themes/theme_candidates.jsonl` is private and never published. The previous file is
rotated to `theme_candidates.prev.jsonl` so the next run can report a trend. Entities that
reached the company threshold but were dropped (generic, overlapping, stoplisted, already
named) go to `theme_candidates_skipped.jsonl` with the reason, for audit. The optional private
stoplist is `themes/candidate_stoplist.yaml`, a YAML list of entity labels or aliases; a missing
file is no stoplist. Each write is a temp
file in the same directory, fsync'd, then `os.replace`d, so a reader sees the old or the new file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import structlog
import yaml
from pydantic import ValidationError

from analytics.supply_chain.entities_io.loader import (
    CATALOG_FILE,
    MENTIONS_FILE,
    EntityFileError,
    parse_entity_files,
)
from analytics.supply_chain.theme_discovery.mining import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_COMPANY_SHARE,
    DEFAULT_MIN_COMPANIES,
    DEFAULT_OVERLAP_THRESHOLD,
    mine,
)
from analytics.supply_chain.theme_discovery.models import (
    PreviousCandidate,
    SkippedCandidate,
    ThemeCandidate,
)
from analytics.supply_chain.themes_io.loader import (
    CONCEPTS_SUBDIR,
    ConceptFileError,
    parse_concept_files,
)

logger = structlog.get_logger(__name__)

ENTITIES_DIR = "entities"
CANDIDATES_FILE = ("themes", "theme_candidates.jsonl")
PREVIOUS_FILE = ("themes", "theme_candidates.prev.jsonl")
SKIPPED_FILE = ("themes", "theme_candidates_skipped.jsonl")
STOPLIST_FILE = ("themes", "candidate_stoplist.yaml")


class ThemeMiningError(ValueError):
    """An input file is unusable; nothing was written."""


@dataclass(frozen=True)
class MiningResult:
    candidates: list[ThemeCandidate]
    written: bool
    path: Path
    skipped: list[SkippedCandidate]


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


def read_stoplist(path: Path) -> list[str]:
    """Labels/aliases to drop. A missing file is no stoplist; a malformed one is an error."""
    if not path.is_file():
        return []
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ThemeMiningError(f"{path.name}: {exc}") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ThemeMiningError(f"{path.name}: expected a YAML list of strings")
    items = [str(x) for x in raw if isinstance(x, str | int | float)]  # pyright: ignore[reportUnknownVariableType]
    return items


def _dump(rows: list[ThemeCandidate] | list[SkippedCandidate]) -> str:
    return "".join(
        json.dumps(r.model_dump(mode="json"), ensure_ascii=False, sort_keys=True) + "\n"
        for r in rows
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


def run_theme_mining(  # noqa: PLR0913 - keyword-only knobs of one entry point
    root: Path,
    *,
    today: date,
    min_companies: int = DEFAULT_MIN_COMPANIES,
    limit: int = DEFAULT_LIMIT,
    max_company_share: float = DEFAULT_MAX_COMPANY_SHARE,
    overlap_threshold: float = DEFAULT_OVERLAP_THRESHOLD,
    industry_groups: Mapping[str, frozenset[str]] | None = None,
    dry_run: bool = False,
) -> MiningResult:
    """Mine and (unless dry) write. Raises `ThemeMiningError` on an invalid input file.

    `industry_groups` (industry-chain theme / sub-industry membership, symbols per key) is
    optional and injected by the caller: the files alone carry no industry roster.
    """
    target = root.joinpath(*CANDIDATES_FILE)
    try:
        parsed = parse_entity_files(root / ENTITIES_DIR)
        concepts = root.joinpath(*CONCEPTS_SUBDIR)
        themes = parse_concept_files(concepts)[0] if concepts.is_dir() else []
        stoplist = read_stoplist(root.joinpath(*STOPLIST_FILE))
    except (EntityFileError, ConceptFileError) as exc:
        raise ThemeMiningError(str(exc)) from exc
    previous = read_previous(target)
    outcome = mine(
        parsed.entities,
        parsed.mentions,
        themes,
        min_companies=min_companies,
        limit=limit,
        previous=previous,
        today=today,
        max_company_share=max_company_share,
        overlap_threshold=overlap_threshold,
        stoplist=stoplist,
        industry_groups=industry_groups,
    )
    candidates = outcome.candidates
    if dry_run:
        return MiningResult(candidates, written=False, path=target, skipped=outcome.skipped)
    if target.is_file():
        write_atomic(root.joinpath(*PREVIOUS_FILE), target.read_text(encoding="utf-8"))
    write_atomic(target, _dump(candidates))
    write_atomic(root.joinpath(*SKIPPED_FILE), _dump(outcome.skipped))
    return MiningResult(candidates, written=True, path=target, skipped=outcome.skipped)


def format_table(candidates: list[ThemeCandidate]) -> str:
    """Compact table: rank, label (a technology/material/application/product name, never a
    company), type, company count, specificity, trend and up to five stock symbols. No company
    name."""
    header = "rank label type companies specificity trend symbols"
    lines = [f"candidates={len(candidates)}", header]
    for rank, c in enumerate(candidates, start=1):
        symbols = ",".join(c.top_companies[:5])
        head = f"{rank} {c.label} {c.type} {c.company_count} {c.specificity:.2f}"
        lines.append(f"{head} {c.trend_text()} {symbols}")
    return "\n".join(lines) + "\n"


__all__ = [
    "CANDIDATES_FILE",
    "PREVIOUS_FILE",
    "SKIPPED_FILE",
    "STOPLIST_FILE",
    "MiningResult",
    "ThemeMiningError",
    "format_table",
    "missing_inputs",
    "read_previous",
    "read_stoplist",
    "run_theme_mining",
    "write_atomic",
]
