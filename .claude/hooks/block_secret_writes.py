#!/usr/bin/env python3
"""PreToolUse hook: block writes to secret files and lockfiles.

Reads the Claude Code hook payload from stdin (JSON) and denies Edit/Write/
MultiEdit calls whose target path is a secret file (.env and variants) or a
managed lockfile. `.env.example` is explicitly allowed (it's the committed
template). Exit 0 = allow; a non-empty `permissionDecision: deny` blocks it.

Rationale: backend/.env holds live Google/LINE/LLM/Paddle/FinMind secrets
(AGENTS.md governance rule 5 — no credentials committed/edited by an agent).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import PurePosixPath

# Blocked path patterns (matched against the basename and the full path, /-normalized).
_BLOCKED = [
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.[^/]+$"),      # .env.local, .env.production, ...
    re.compile(r"(^|/)[^/]+\.pem$"),
    re.compile(r"(^|/)[^/]+\.key$"),
    re.compile(r"(^|/)secrets/"),
]
# Allowlist exceptions (templates are safe to edit).
_ALLOWED = [
    re.compile(r"(^|/)\.env\.example$"),
    re.compile(r"(^|/)\.env\.sample$"),
    re.compile(r"(^|/)\.env\.template$"),
]


def _target_paths(payload: dict) -> list[str]:
    """Extract candidate file paths from a tool-use payload (schema-tolerant)."""
    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    paths: list[str] = []
    for key in ("file_path", "filePath", "path", "notebook_path"):
        if isinstance(ti.get(key), str):
            paths.append(ti[key])
    # MultiEdit / batched edits
    edits = ti.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict) and isinstance(e.get("file_path"), str):
                paths.append(e["file_path"])
    return paths


def _is_blocked(path: str) -> bool:
    norm = str(PurePosixPath(path.replace("\\", "/")))
    if any(rx.search(norm) for rx in _ALLOWED):
        return False
    return any(rx.search(norm) for rx in _BLOCKED)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # can't parse → don't block (fail-open on malformed input)

    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if tool not in {"Edit", "Write", "MultiEdit", "NotebookEdit"}:
        return 0

    blocked = [p for p in _target_paths(payload) if _is_blocked(p)]
    if not blocked:
        return 0

    reason = (
        f"Blocked write to secret file(s): {', '.join(blocked)}. "
        "Secrets (.env, *.pem, *.key, secrets/) must not be edited by an agent "
        "(AGENTS.md governance rule 5). Edit .env.example instead, or ask the "
        "user to change the real .env by hand."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
