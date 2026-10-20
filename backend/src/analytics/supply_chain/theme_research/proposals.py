"""The two proposal shapes `promote` reads, built from verified members and written atomically.

* `proposals/new_themes_<UTC date>.yaml` - rows `{key, name_zh, name_en, description, related,
  thin, members: [...]}`, one per candidate theme with enough verified members.
* `proposals/theme_members_<UTC date>.yaml` - rows `{theme_key, symbol, name, position, publisher,
  url, excerpt, confidence}` for members of existing themes.

Nothing here edits a live concept file; a person reviews these files and runs `promote`. Writing
merges with what the day's file already holds (union, never a duplicate), so a re-run changes
nothing. A pair already present in ANY proposal file is not proposed again.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Final, cast

import yaml

from analytics.supply_chain.theme_discovery.io import write_atomic
from analytics.supply_chain.theme_research.files import proposals_dir
from analytics.supply_chain.themes_io.promote import ProposalFileError

NEW_THEMES_PREFIX: Final = "new_themes_"
MEMBERS_PREFIX: Final = "theme_members_"
MIN_NEW_THEME_MEMBERS: Final = 6
NEW_THEME_DESCRIPTION: Final = "Proposed from web research; pending review."

Row = dict[str, Any]


@dataclass
class ProposedMember:
    """A verified member, in the field order of a promote row."""

    symbol: str
    name: str
    position: str
    publisher: str
    url: str
    excerpt: str
    confidence: str

    def row(self) -> Row:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "position": self.position,
            "publisher": self.publisher,
            "url": self.url,
            "excerpt": self.excerpt,
            "confidence": self.confidence,
        }


@dataclass
class ProposedTheme:
    key: str
    name_zh: str
    name_en: str
    members: list[ProposedMember] = field(default_factory=lambda: [])

    def row(self) -> Row:
        return {
            "key": self.key,
            "name_zh": self.name_zh,
            "name_en": self.name_en,
            "description": NEW_THEME_DESCRIPTION,
            "related": [],
            "thin": False,
            "members": [m.row() for m in self.members],
        }


@dataclass(frozen=True)
class ProposalPaths:
    new_themes: Path
    members: Path


@dataclass(frozen=True)
class Proposed:
    """What earlier proposal files already hold: (theme key, symbol) pairs and new-theme keys."""

    pairs: frozenset[tuple[str, str]] = frozenset()
    theme_keys: frozenset[str] = frozenset()


def proposal_paths(root: Path, today: date) -> ProposalPaths:
    base = proposals_dir(root)
    stamp = today.isoformat()
    return ProposalPaths(
        new_themes=base / f"{NEW_THEMES_PREFIX}{stamp}.yaml",
        members=base / f"{MEMBERS_PREFIX}{stamp}.yaml",
    )


def read_rows(path: Path) -> list[Row]:
    """The rows of a proposal file; a missing file has none, an unreadable one is an error."""
    if not path.is_file():
        return []
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProposalFileError(f"{path.name}: cannot read ({exc})") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProposalFileError(f"{path.name}: expected a YAML list of proposal rows")
    return [cast("Row", r) for r in cast("list[object]", raw) if isinstance(r, dict)]


def _symbols(row: Row) -> list[str]:
    members = row.get("members")
    if not isinstance(members, list):
        return []
    found: list[str] = []
    for item in cast("list[object]", members):
        if isinstance(item, dict):
            found.append(str(cast("Row", item).get("symbol", "")).strip())
    return found


def scan_proposed(root: Path) -> Proposed:
    """Every pair and new-theme key in the proposal files of any date."""
    pairs: set[tuple[str, str]] = set()
    keys: set[str] = set()
    base = proposals_dir(root)
    if not base.is_dir():
        return Proposed()
    files = [*base.glob(f"{NEW_THEMES_PREFIX}*.yaml"), *base.glob(f"{MEMBERS_PREFIX}*.yaml")]
    for path in sorted(files):
        for row in read_rows(path):
            if "members" in row:
                key = str(row.get("key", "")).strip()
                keys.add(key)
                pairs.update((key, s) for s in _symbols(row))
            elif "theme_key" in row:
                pairs.add((str(row["theme_key"]).strip(), str(row.get("symbol", "")).strip()))
    return Proposed(frozenset(pairs), frozenset(keys))


def _merge_themes(existing: list[Row], themes: list[ProposedTheme]) -> tuple[list[Row], int, int]:
    rows = [dict(r) for r in existing]
    by_key = {str(r.get("key", "")): r for r in rows if "members" in r}
    new_themes = added = 0
    for theme in themes:
        current = by_key.get(theme.key)
        if current is None:
            row = theme.row()
            rows.append(row)
            by_key[theme.key] = row
            new_themes += 1
            added += len(theme.members)
            continue
        have = set(_symbols(current))
        members = cast("list[Row]", current.setdefault("members", []))
        for member in theme.members:
            if member.symbol not in have:
                members.append(member.row())
                have.add(member.symbol)
                added += 1
    return rows, new_themes, added


def _merge_members(existing: list[Row], additions: list[tuple[str, ProposedMember]]) -> list[Row]:
    rows = [dict(r) for r in existing]
    seen = {(str(r.get("theme_key", "")), str(r.get("symbol", ""))) for r in rows}
    for key, member in additions:
        if (key, member.symbol) not in seen:
            seen.add((key, member.symbol))
            rows.append({"theme_key": key, **member.row()})
    return rows


@dataclass(frozen=True)
class WriteResult:
    new_themes: int
    member_rows: int


def _dump(rows: list[Row]) -> str:
    return yaml.safe_dump(rows, allow_unicode=True, sort_keys=False)


def write_proposals(
    paths: ProposalPaths,
    *,
    themes: list[ProposedTheme],
    additions: list[tuple[str, ProposedMember]],
    dry_run: bool,
) -> WriteResult:
    """Merge into the day's files and replace them atomically. Returns what was ADDED, so a
    re-run reports zero. A dry run computes the same numbers and writes nothing."""
    theme_rows, new_themes, theme_members = _merge_themes(read_rows(paths.new_themes), themes)
    before = read_rows(paths.members)
    member_rows = _merge_members(before, additions)
    added = len(member_rows) - len(before) + theme_members
    if not dry_run:
        if themes:
            write_atomic(paths.new_themes, _dump(theme_rows))
        if additions:
            write_atomic(paths.members, _dump(member_rows))
    return WriteResult(new_themes=new_themes, member_rows=added)


_NON_SLUG = re.compile(r"[^a-z0-9]+")
_MIN_SLUG = 2


def theme_key_for(candidate_id: str, names: list[str]) -> tuple[str, str]:
    """(key, name_en) for a candidate theme: the first ASCII name's slug, else the candidate id.
    The key is stable across runs because it depends on the label and aliases only."""
    for name in names:
        slug = _NON_SLUG.sub("-", name.lower()).strip("-")
        if len(slug) >= _MIN_SLUG and name.isascii():
            return slug, name.strip()
    return candidate_id, ""


__all__ = [
    "MEMBERS_PREFIX",
    "MIN_NEW_THEME_MEMBERS",
    "NEW_THEMES_PREFIX",
    "NEW_THEME_DESCRIPTION",
    "ProposalPaths",
    "Proposed",
    "ProposedMember",
    "ProposedTheme",
    "WriteResult",
    "proposal_paths",
    "read_rows",
    "scan_proposed",
    "theme_key_for",
    "write_proposals",
]
