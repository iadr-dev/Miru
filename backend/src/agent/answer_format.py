"""Deterministic clean-up of answer formatting habits a prompt rule alone cannot guarantee."""

from __future__ import annotations

import re

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


__all__ = ["strip_tldr"]
