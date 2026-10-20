"""Promote reviewed proposal files into the live concept-theme files.

The explicit, repeatable operator step behind `python -m analytics.supply_chain promote`; never
part of a cron. A proposal file is a YAML list whose rows have one of two shapes, detected per row:

* new theme: `{key, name_zh, name_en, description, related, thin, members: [...]}`
* member addition: `{theme_key, symbol, name, position, publisher, url, excerpt, confidence}`

Every row and member passes the checks below; a failure is a counted reject reason, never a
silent drop. Accepted members get origin `proposed`. Before any write the whole concepts directory
is copied to `themes/_backups/concepts-<UTC timestamp>/` (the 5 newest are kept); every resulting
file is validated first, and one failure writes nothing. Reports carry symbols and counts only,
never a company name.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import ValidationError

from analytics.supply_chain.domain.concepts import ConceptOrigin, ConceptPosition, EntityType
from analytics.supply_chain.entities_io.loader import (
    CATALOG_FILE,
    ENTITIES_SUBDIR,
    EntityFileError,
    parse_catalog,
)
from analytics.supply_chain.theme_discovery.io import write_atomic
from analytics.supply_chain.theme_discovery.mining import normalize
from analytics.supply_chain.themes_io.loader import (
    CONCEPTS_SUBDIR,
    ConceptFileError,
    build_theme,
    parse_concept_files,
)
from analytics.supply_chain.themes_io.schema import ConceptMemberFileRow, ConceptThemeFile

BACKUPS_SUBDIR = ("themes", "_backups")
LOG_FILE = ("themes", "promote_log.jsonl")
KEEP_BACKUPS = 5
MAX_EXCERPT = 200
MIN_NAME_LENGTH = 2
CONFIDENCE_RANK = {"high": 2, "medium": 1}

# Reject reasons (stable ids, counted in the report and the log).
UNKNOWN_THEME = "unknown_theme"
THEME_EXISTS = "theme_exists"
INDUSTRY_COLLISION = "industry_collision"
UNKNOWN_SYMBOL = "unknown_symbol"
INVALID_POSITION = "invalid_position"
EVIDENCE_MISSING = "evidence_missing"
LOW_CONFIDENCE = "low_confidence"
DUPLICATE_SYMBOL = "duplicate_symbol"
ALREADY_MEMBER = "already_member"
SCHEMA_INVALID = "schema_invalid"
INVALID_ROW = "invalid_row"
NO_VALID_MEMBERS = "no_valid_members"

Raw = dict[str, Any]


class ProposalFileError(ValueError):
    """A proposal (or existing concept) file is unusable; nothing was written."""


class PromoteWriteError(ValueError):
    """A resulting concept file failed validation; nothing was written."""


@dataclass(frozen=True)
class IndustryRoster:
    """Industry-chain themes as the database holds them (keys, display names, listed symbols)."""

    keys: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()  # normalised zh/en names
    symbols: frozenset[str] = frozenset()


class PromoteReport:
    def __init__(self) -> None:
        self.added: Counter[str] = Counter()  # theme key -> members added
        self.new_themes: list[str] = []
        self.rejects: Counter[str] = Counter()
        self.reject_symbols: dict[str, list[str]] = {}
        self.warnings: list[str] = []
        self.written = False

    def reject(self, reason: str, symbol: str | None = None) -> None:
        self.rejects[reason] += 1
        if symbol:
            self.reject_symbols.setdefault(reason, []).append(symbol)

    def format(self, *, dry_run: bool) -> str:
        lines = [f"warning: {w}" for w in self.warnings]
        lines.append(f"new_themes={len(self.new_themes)} added_members={sum(self.added.values())}")
        lines.extend(f"theme {k}: +{n} members" for k, n in sorted(self.added.items()))
        for reason, n in sorted(self.rejects.items()):
            syms = ",".join(self.reject_symbols.get(reason, [])[:10])
            lines.append(f"rejected {reason}={n}" + (f" symbols={syms}" if syms else ""))
        lines.append("dry run: nothing written" if dry_run else "written")
        return "\n".join(lines) + "\n"


@dataclass
class _Theme:
    raw: Raw
    path: Path
    changed: bool = False


@dataclass(frozen=True)
class _Roster:
    symbols: frozenset[str]
    names: dict[str, set[str]]  # symbol -> normalised catalog names and aliases


PROPOSAL_PATH_MESSAGE = "proposal must be a .yaml file inside the reference data directory"


def safe_proposal_path(root: Path, candidate: Path) -> Path:
    """The resolved proposal path, or `ProposalFileError`. Symlinks are resolved first, so a link
    pointing outside the reference directory is rejected. Read only from the returned value."""
    base = root.resolve(strict=False)
    resolved = Path(candidate).resolve(strict=False)
    if (
        resolved.suffix.lower() not in {".yaml", ".yml"}
        or not resolved.is_relative_to(base)
        or not resolved.is_file()
    ):
        raise ProposalFileError(f"{candidate.name}: {PROPOSAL_PATH_MESSAGE}")
    return resolved


def _read_proposals(path: Path) -> list[object]:
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProposalFileError(f"{path.name}: cannot read ({exc})") from exc
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProposalFileError(f"{path.name}: expected a YAML list of proposal rows")
    return list(cast("list[object]", raw))


def _roster(root: Path, industry: IndustryRoster | None) -> _Roster:
    symbols: set[str] = set(industry.symbols) if industry else set()
    names: dict[str, set[str]] = {}
    catalog = root.joinpath(*ENTITIES_SUBDIR, CATALOG_FILE)
    if catalog.is_file():
        try:
            entities = parse_catalog(catalog)
        except EntityFileError as exc:
            raise ProposalFileError(str(exc)) from exc
        for e in entities:
            if e.type is EntityType.TW_COMPANY and e.symbol and e.market == "TW":
                symbols.add(e.symbol)
                bucket = names.setdefault(e.symbol, set())
                bucket.update(n for n in (normalize(x) for x in (e.id, *e.aliases)) if n)
    return _Roster(frozenset(symbols), names)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _evidence_ok(row: Raw, catalog_names: set[str]) -> bool:
    url, excerpt = _text(row.get("url")), _text(row.get("excerpt"))
    if not url.lower().startswith("https://") or len(url) <= len("https://"):
        return False
    if not excerpt or len(excerpt) > MAX_EXCERPT:
        return False
    haystack = normalize(excerpt)
    names = {
        n for n in (normalize(_text(row.get("name"))), *catalog_names) if len(n) >= MIN_NAME_LENGTH
    }
    return any(n in haystack for n in names)


def _file_stem(key: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in key)


class _Promoter:
    def __init__(  # noqa: PLR0913, PLR0917 - the state of one run
        self,
        themes: dict[str, _Theme],
        roster: _Roster,
        industry: IndustryRoster | None,
        concepts_dir: Path,
        min_rank: int,
        report: PromoteReport,
    ) -> None:
        self.themes = themes
        self.roster = roster
        self.industry = industry
        self.concepts_dir = concepts_dir
        self.min_rank = min_rank
        self.report = report

    def _concept_names(self) -> set[str]:
        out: set[str] = set()
        for t in self.themes.values():
            out.update(
                n for n in (normalize(_text(t.raw.get(k))) for k in ("name_zh", "name_en")) if n
            )
        return out

    def _check_member(  # noqa: PLR0911 - one early return per reject reason
        self, row: Raw, theme: Raw
    ) -> tuple[str, Raw | None]:
        """(reject reason, None) or ('', the member mapping to write)."""
        symbol = str(row.get("symbol", "")).strip()
        if symbol not in self.roster.symbols:
            return UNKNOWN_SYMBOL, None
        if row.get("position") not in {p.value for p in ConceptPosition}:
            return INVALID_POSITION, None
        if not _evidence_ok(row, self.roster.names.get(symbol, set())):
            return EVIDENCE_MISSING, None
        if CONFIDENCE_RANK.get(str(row.get("confidence", "")).strip().lower(), 0) < self.min_rank:
            return LOW_CONFIDENCE, None
        member: Raw = {
            "symbol": symbol,
            "name": _text(row.get("name")),
            "position": row["position"],
            "origin": ConceptOrigin.PROPOSED.value,
        }
        try:
            ConceptMemberFileRow.model_validate(member)
        except ValidationError:
            return SCHEMA_INVALID, None
        members = cast("list[Raw]", theme.get("members") or [])
        if any(str(m.get("symbol")) == symbol for m in members):
            return ALREADY_MEMBER, None
        return "", member

    def member_row(self, row: Raw) -> None:
        key = _text(row.get("theme_key"))
        symbol = str(row.get("symbol", "")).strip()
        target = self.themes.get(key)
        if target is None:
            self.report.reject(UNKNOWN_THEME, symbol)
            return
        reason, member = self._check_member(row, target.raw)
        if member is None:
            self.report.reject(reason, symbol)
            return
        target.raw.setdefault("members", []).append(member)
        target.changed = True
        self.report.added[key] += 1

    def _theme_conflict(self, row: Raw, key: str, name_zh: str) -> str:
        name_en = normalize(_text(row.get("name_en")))
        if self.industry is not None and (
            key in self.industry.keys
            or normalize(name_zh) in self.industry.names
            or (name_en and name_en in self.industry.names)
        ):
            return INDUSTRY_COLLISION
        path = self.concepts_dir / f"{_file_stem(key)}.yaml"
        if key in self.themes or normalize(name_zh) in self._concept_names() or path.exists():
            return THEME_EXISTS
        return ""

    def theme_row(self, row: Raw) -> None:
        key, name_zh = _text(row.get("key")), _text(row.get("name_zh"))
        head: Raw = {
            "key": key,
            "name_zh": name_zh,
            "name_en": _text(row.get("name_en")),
            "description": _text(row.get("description")),
            "related": [str(x) for x in cast("list[object]", row.get("related") or [])],
        }
        try:
            ConceptThemeFile.model_validate(head)
        except ValidationError:
            self.report.reject(SCHEMA_INVALID)
            return
        conflict = self._theme_conflict(row, key, name_zh)
        if conflict:
            self.report.reject(conflict)
            return
        raw: Raw = {**head, "members": []}
        seen: set[str] = set()
        for item in cast("list[object]", row.get("members") or []):
            if not isinstance(item, dict):
                self.report.reject(INVALID_ROW)
                continue
            member_row = cast("Raw", item)
            symbol = str(member_row.get("symbol", "")).strip()
            if symbol in seen:
                self.report.reject(DUPLICATE_SYMBOL, symbol)
                continue
            reason, member = self._check_member(member_row, raw)
            if member is None:
                self.report.reject(reason, symbol)
                continue
            seen.add(symbol)
            raw["members"].append(member)
        if not raw["members"]:
            self.report.reject(NO_VALID_MEMBERS)
            return
        path = self.concepts_dir / f"{_file_stem(key)}.yaml"
        self.themes[key] = _Theme(raw=raw, path=path, changed=True)
        self.report.new_themes.append(key)
        self.report.added[key] += len(raw["members"])

    def row(self, item: object) -> None:
        if not isinstance(item, dict):
            self.report.reject(INVALID_ROW)
            return
        row = cast("Raw", item)
        if "members" in row:
            self.theme_row(row)
        elif "theme_key" in row:
            self.member_row(row)
        else:
            self.report.reject(INVALID_ROW)


def _validate_result(theme: _Theme) -> None:
    try:
        build_theme(ConceptThemeFile.model_validate(theme.raw), theme.path.name)
    except (ValidationError, ConceptFileError) as exc:
        raise PromoteWriteError(f"{theme.path.name}: resulting file is invalid") from exc


def _dump(raw: Raw) -> str:
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)


def backup_concepts(root: Path, now: datetime) -> Path | None:
    """Copy the concepts dir to `_backups/concepts-<UTC timestamp>/`, keep the newest 5."""
    source = root.joinpath(*CONCEPTS_SUBDIR)
    if not source.is_dir():
        return None
    base = root.joinpath(*BACKUPS_SUBDIR)
    target = base / f"concepts-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    shutil.copytree(source, target)
    existing = sorted(p for p in base.glob("concepts-*") if p.is_dir())
    for old in existing[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)
    return target


def _load_existing(concepts_dir: Path) -> dict[str, _Theme]:
    if not concepts_dir.is_dir():
        return {}
    try:
        parse_concept_files(concepts_dir)  # validates every file and rejects duplicate keys
    except ConceptFileError as exc:
        raise ProposalFileError(str(exc)) from exc
    themes: dict[str, _Theme] = {}
    for path in sorted(concepts_dir.glob("*.yaml")):
        raw = cast("Raw", yaml.safe_load(path.read_text(encoding="utf-8")))
        themes[str(raw["key"])] = _Theme(raw=raw, path=path)
    return themes


def run_promote(  # noqa: PLR0913 - keyword-only knobs of one entry point
    root: Path,
    files: list[Path],
    *,
    dry_run: bool = False,
    min_confidence: str = "medium",
    industry: IndustryRoster | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PromoteReport:
    """`industry=None` means the database was unreachable: the industry key check is skipped and
    the symbol roster is the entity catalog only (reported as a warning).

    Raises `ProposalFileError` for an unusable input or concept file and `PromoteWriteError` when a
    resulting file would be invalid; in both cases nothing was written."""
    report = PromoteReport()
    if industry is None:
        report.warnings.append(
            "industry roster unavailable (database): industry key collisions are NOT checked "
            "and the symbol roster is the entity catalog only"
        )
    concepts_dir = root.joinpath(*CONCEPTS_SUBDIR)
    themes = _load_existing(concepts_dir)
    safe_files = [safe_proposal_path(root, p) for p in files]
    proposals = [(p, _read_proposals(p)) for p in safe_files]
    promoter = _Promoter(
        themes,
        _roster(root, industry),
        industry,
        concepts_dir,
        CONFIDENCE_RANK[min_confidence],
        report,
    )
    for _, rows in proposals:
        for item in rows:
            promoter.row(item)
    changed = [t for t in themes.values() if t.changed]
    for theme in changed:
        _validate_result(theme)
    if dry_run:
        return report
    stamp = now()
    if changed:
        backup_concepts(root, stamp)
        for theme in changed:
            write_atomic(theme.path, _dump(theme.raw))
        report.written = True
    _append_log(root, stamp, [p.name for p, _ in proposals], report)
    return report


def _append_log(root: Path, stamp: datetime, names: list[str], report: PromoteReport) -> None:
    entry = {
        "timestamp": stamp.isoformat(),
        "inputs": names,
        "new_themes": len(report.new_themes),
        "added_members": sum(report.added.values()),
        "rejects": dict(sorted(report.rejects.items())),
    }
    path = root.joinpath(*LOG_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


__all__ = [
    "IndustryRoster",
    "PromoteReport",
    "PromoteWriteError",
    "ProposalFileError",
    "backup_concepts",
    "run_promote",
]
