"""`rejected.jsonl`: the append-only record of claims that were examined and not supported.

One JSON object per line, `{"claim_id", "rejected_on", "reason"}`. A claim in it is not tried again
until the cooldown passes. Appends are a single `O_APPEND` write, so concurrent writers never
interleave inside a line; a torn or foreign line is skipped on read.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import structlog

logger = structlog.get_logger(__name__)

REJECTED_FILE = ("supply_chain", "rejected.jsonl")


def rejected_path(root: Path) -> Path:
    return root.joinpath(*REJECTED_FILE)


def load_recent_rejections(path: Path, *, today: date, cooldown_days: int) -> set[str]:
    """Claim ids rejected within the cooldown window."""
    if not path.is_file():
        return set()
    earliest = today - timedelta(days=cooldown_days)
    recent: set[str] = set()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.warning("sc_rejected_log_unreadable")
        return set()
    for line in lines:
        try:
            raw: object = json.loads(line)
            if not isinstance(raw, dict):
                continue
            fields = cast("dict[str, object]", raw)
            claim_id, on = fields.get("claim_id"), fields.get("rejected_on")
            if (
                isinstance(claim_id, str)
                and isinstance(on, str)
                and date.fromisoformat(on) >= earliest
            ):
                recent.add(claim_id)
        except ValueError:
            continue
    return recent


def append_rejection(path: Path, *, claim_id: str, on: date, reason: str) -> None:
    line = json.dumps(
        {"claim_id": claim_id, "rejected_on": on.isoformat(), "reason": reason},
        ensure_ascii=False,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (line + "\n").encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


__all__ = ["REJECTED_FILE", "append_rejection", "load_recent_rejections", "rejected_path"]
