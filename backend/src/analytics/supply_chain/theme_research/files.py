"""The private files of theme research, all under `<reference dir>/themes/`.

* `proposals/claims.jsonl` - raw claims from Step B, append-only, one JSON object per line,
  de-duplicated on (theme, symbol, url). A torn or foreign line is skipped on read.
* `proposals/rejected.jsonl` - claims Step C turned down: theme, symbol, url, date and a reason
  code. No company name.
* `research_state.json` - when each concept theme / candidate was last researched, so the next
  run takes the least recently researched first. Replaced atomically.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import cast

import structlog
from pydantic import ValidationError

from analytics.supply_chain.theme_discovery.io import write_atomic
from analytics.supply_chain.theme_research.models import ThemeClaim

logger = structlog.get_logger(__name__)

PROPOSALS_SUBDIR = ("themes", "proposals")
CLAIMS_FILE = ("themes", "proposals", "claims.jsonl")
REJECTED_FILE = ("themes", "proposals", "rejected.jsonl")
STATE_FILE = ("themes", "research_state.json")


def claims_path(root: Path) -> Path:
    return root.joinpath(*CLAIMS_FILE)


def rejected_path(root: Path) -> Path:
    return root.joinpath(*REJECTED_FILE)


def state_path(root: Path) -> Path:
    return root.joinpath(*STATE_FILE)


def proposals_dir(root: Path) -> Path:
    return root.joinpath(*PROPOSALS_SUBDIR)


def _append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "".join(line + "\n" for line in lines).encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)


def read_claims(path: Path) -> list[ThemeClaim]:
    if not path.is_file():
        return []
    claims: list[ThemeClaim] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.warning("theme_claims_unreadable")
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            claims.append(ThemeClaim.model_validate(json.loads(line)))
        except (ValueError, ValidationError):
            continue
    return claims


def append_claims(path: Path, claims: Iterable[ThemeClaim]) -> tuple[int, int]:
    """Append the claims not already in the file. Returns (appended, duplicates)."""
    seen = {c.dedupe_key for c in read_claims(path)}
    fresh: list[ThemeClaim] = []
    duplicates = 0
    for claim in claims:
        if claim.dedupe_key in seen:
            duplicates += 1
            continue
        seen.add(claim.dedupe_key)
        fresh.append(claim)
    _append_lines(path, [json.dumps(c.model_dump(mode="json"), ensure_ascii=False) for c in fresh])
    return len(fresh), duplicates


def read_rejected_keys(path: Path) -> set[tuple[str, str, str]]:
    """(theme, symbol, url) of every rejected claim."""
    if not path.is_file():
        return set()
    keys: set[tuple[str, str, str]] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    for line in lines:
        try:
            raw: object = json.loads(line)
        except ValueError:
            continue
        if not isinstance(raw, dict):
            continue
        fields = cast("dict[str, object]", raw)
        theme, symbol, url = fields.get("theme"), fields.get("symbol"), fields.get("url")
        if isinstance(theme, str) and isinstance(symbol, str) and isinstance(url, str):
            keys.add((theme, symbol, url))
    return keys


def append_rejections(path: Path, rejections: Iterable[tuple[ThemeClaim, str]], *, on: date) -> int:
    """Append rejected claims not already logged. Returns how many were added."""
    seen = read_rejected_keys(path)
    lines: list[str] = []
    for claim, reason in rejections:
        if claim.dedupe_key in seen:
            continue
        seen.add(claim.dedupe_key)
        entry = {
            "theme": claim.theme,
            "symbol": claim.symbol,
            "url": claim.url,
            "rejected_on": on.isoformat(),
            "reason": reason,
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    _append_lines(path, lines)
    return len(lines)


class ResearchState:
    """Last-researched dates: `themes` (concept theme key) and `candidates` (candidate id)."""

    def __init__(self, themes: dict[str, str], candidates: dict[str, str]) -> None:
        self.themes = themes
        self.candidates = candidates

    def mark(self, kind: str, target_id: str, on: date) -> None:
        (self.candidates if kind == "candidate" else self.themes)[target_id] = on.isoformat()

    def last(self, kind: str, target_id: str) -> str:
        return (self.candidates if kind == "candidate" else self.themes).get(target_id, "")


def _dates(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    return {
        k: v
        for k, v in cast("dict[object, object]", raw).items()
        if isinstance(k, str) and isinstance(v, str)
    }


def read_state(path: Path) -> ResearchState:
    if not path.is_file():
        return ResearchState({}, {})
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("theme_research_state_unreadable")
        return ResearchState({}, {})
    if not isinstance(raw, dict):
        return ResearchState({}, {})
    fields = cast("dict[str, object]", raw)
    return ResearchState(_dates(fields.get("themes")), _dates(fields.get("candidates")))


def write_state(path: Path, state: ResearchState) -> None:
    body = {"themes": state.themes, "candidates": state.candidates}
    write_atomic(path, json.dumps(body, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


__all__ = [
    "CLAIMS_FILE",
    "PROPOSALS_SUBDIR",
    "REJECTED_FILE",
    "STATE_FILE",
    "ResearchState",
    "append_claims",
    "append_rejections",
    "claims_path",
    "proposals_dir",
    "read_claims",
    "read_rejected_keys",
    "read_state",
    "rejected_path",
    "state_path",
    "write_state",
]
