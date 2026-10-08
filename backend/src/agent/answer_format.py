"""Deterministic clean-up of answer formatting habits a prompt rule alone cannot guarantee."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import cast

# A line that opens with "TL;DR" (optionally bold/bulleted, ASCII or fullwidth colon): the model
# restating its own answer as a closing summary. Governance rule 13 asks it not to; some models do
# it anyway, so the final answer drops the line.
_TLDR_LINE = re.compile(r"^\s*(?:[-*>]\s*)?(?:\*\*|__)?\s*TL\s*;?\s*DR\b.*$", re.IGNORECASE)


def strip_tldr(text: str) -> str:
    """The answer without any 'TL;DR' summary line. Untouched when there is none."""
    lines = text.split("\n")
    kept = [line for line in lines if not _TLDR_LINE.match(line)]
    if len(kept) == len(lines):
        return text
    cleaned = "\n".join(kept)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def recover_textual_tool_call(
    text: str, known_tools: Collection[str]
) -> tuple[str, dict[str, object]] | None:
    """A tool call the model wrote out as its reply instead of calling it.

    Some models (seen: Gemini) now and then answer with the call as plain JSON -
    `{"name": "propose_watchlist_change", "arguments": {...}}` - and no function call, so the user
    sees raw JSON and nothing happens. When the WHOLE reply is exactly one such object naming a
    tool this turn actually has, it is treated as the call it was meant to be. Anything else (prose
    around it, an unknown tool, extra keys, arguments that are not an object) is left alone, so
    ordinary answers and quoted JSON are never reinterpreted.
    """
    obj = _json_object(text)
    if obj is None or set(obj) - {"name", "arguments"} or "name" not in obj:
        return None
    name, arguments = obj["name"], obj.get("arguments", {})
    if not isinstance(name, str) or not isinstance(arguments, dict):
        return None
    bare = name.rsplit(":", 1)[-1]
    if bare not in known_tools:
        return None
    return bare, {str(key): value for key, value in cast("dict[object, object]", arguments).items()}


def _json_object(text: str) -> dict[str, object] | None:
    """`text` (optionally in a code fence) as a JSON object, or None if it is anything else."""
    candidate = text.strip()
    fenced = _FENCE.match(candidate)
    if fenced:
        candidate = fenced.group(1)
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return None
    try:
        loaded: object = json.loads(candidate)
    except ValueError:
        return None
    return cast("dict[str, object]", loaded) if isinstance(loaded, dict) else None


__all__ = ["recover_textual_tool_call", "strip_tldr"]
