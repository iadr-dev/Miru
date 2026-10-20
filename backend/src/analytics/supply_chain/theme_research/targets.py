"""Which themes to research next. Reads the private files; nothing is searched here.

Two sources: the top unresearched entries of `themes/theme_candidates.jsonl` (new-theme proposals,
file order = Step A's ranking) and the existing concept themes, least recently researched first
(never researched before any dated one), looking for missing members.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path

import structlog
from pydantic import ValidationError

from analytics.supply_chain.domain.concepts import Entity
from analytics.supply_chain.entities_io.loader import CATALOG_FILE, EntityFileError, parse_catalog
from analytics.supply_chain.theme_discovery.io import CANDIDATES_FILE
from analytics.supply_chain.theme_discovery.mining import covered_names, normalize
from analytics.supply_chain.theme_discovery.models import ThemeCandidate
from analytics.supply_chain.theme_research.files import ResearchState
from analytics.supply_chain.theme_research.models import ThemeTarget
from analytics.supply_chain.themes_io.loader import CONCEPTS_SUBDIR, parse_concept_files

logger = structlog.get_logger(__name__)

SOURCES = ("candidates", "existing", "all")


def read_theme_candidates(path: Path) -> list[ThemeCandidate]:
    """Tolerant: a blank, torn or foreign line is skipped. A missing file is no candidates."""
    if not path.is_file():
        return []
    rows: list[ThemeCandidate] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(ThemeCandidate.model_validate(json.loads(line)))
        except (ValueError, ValidationError):
            continue
    return rows


def read_catalog(root: Path) -> list[Entity]:
    path = root / "entities" / CATALOG_FILE
    if not path.is_file():
        return []
    try:
        return parse_catalog(path)
    except EntityFileError:
        logger.warning("theme_research_catalog_unreadable")
        return []


def queries_for(target: ThemeTarget) -> list[str]:
    """Two queries from the theme label: the concept-stock list, then the Taiwan supply chain."""
    return [f"{target.label} 概念股", f"{target.label} 供應鏈 台灣"]


def _unique(names: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for name in names:
        folded = normalize(name)
        if folded and folded not in seen:
            seen.add(folded)
            out.append(name.strip())
    return tuple(out)


def candidate_targets(
    root: Path, state: ResearchState, catalog: list[Entity], covered: set[str]
) -> list[ThemeTarget]:
    by_id = {e.id: e for e in catalog}
    out: list[ThemeTarget] = []
    for c in read_theme_candidates(root.joinpath(*CANDIDATES_FILE)):
        if state.last("candidate", c.candidate_id) or normalize(c.label) in covered:
            continue
        entity = by_id.get(c.entity_id)
        out.append(
            ThemeTarget(
                target_id=c.candidate_id,
                kind="candidate",
                label=c.label,
                names=_unique([c.label, *(entity.aliases if entity else ())]),
            )
        )
    return out


def existing_targets(root: Path, state: ResearchState) -> list[ThemeTarget]:
    concepts = root.joinpath(*CONCEPTS_SUBDIR)
    if not concepts.is_dir():
        return []
    themes = parse_concept_files(concepts)[0]
    ordered = sorted(themes, key=lambda t: (state.last("existing", t.key), t.key))
    return [
        ThemeTarget(
            target_id=t.key,
            kind="existing",
            label=t.name_zh,
            names=_unique([t.name_zh, t.name_en]),
            members=frozenset(m.symbol for m in t.members),
        )
        for t in ordered
    ]


def covered_theme_names(root: Path) -> set[str]:
    concepts = root.joinpath(*CONCEPTS_SUBDIR)
    return covered_names(parse_concept_files(concepts)[0]) if concepts.is_dir() else set()


def select_targets(
    root: Path, state: ResearchState, *, source: str, limit: int
) -> list[ThemeTarget]:
    """`--source all` gives candidates the first half of the limit and existing themes the rest;
    room one source does not use flows to the other. Raises `ConceptFileError` on a bad theme
    file."""
    if limit <= 0:
        return []
    fresh: list[ThemeTarget] = []
    if source in ("candidates", "all"):
        fresh = candidate_targets(root, state, read_catalog(root), covered_theme_names(root))
    existing = existing_targets(root, state) if source in ("existing", "all") else []
    if source == "candidates":
        return fresh[:limit]
    if source == "existing":
        return existing[:limit]
    first = fresh[: math.ceil(limit / 2)]
    rest = existing[: limit - len(first)]
    extra = fresh[len(first) : len(first) + (limit - len(first) - len(rest))]
    return [*first, *rest, *extra]


__all__ = [
    "SOURCES",
    "candidate_targets",
    "existing_targets",
    "queries_for",
    "read_catalog",
    "read_theme_candidates",
    "select_targets",
]
