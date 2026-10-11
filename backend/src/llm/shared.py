"""Helpers shared by the LLM provider clients."""

from __future__ import annotations

import json
from typing import cast


def parse_json_object(raw: str) -> dict[str, object]:
    """Parse a (possibly empty/malformed) assembled tool-call-arguments JSON fragment into a
    typed `dict[str, object]`, never raising and never letting an `Any` escape this module."""
    if not raw:
        return {}
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return cast("dict[str, object]", parsed) if isinstance(parsed, dict) else {}


__all__ = ["parse_json_object"]
