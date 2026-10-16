"""Intent -> tool ranking for `search_tools` (pure functions, no I/O).

Plain weighted scoring over each tool's name and description, no embeddings: whole-word hits on
the name count most, then substring hits on the name, then description words; CJK text (the
zh-TW tool descriptions / user questions) is matched on overlapping character pairs because it
has no spaces to split on. Ties break on tool name so results are deterministic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

_WORD = re.compile(r"[a-z0-9]+")
_CJK = re.compile(r"[㐀-鿿]+")
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "do",
        "for",
        "from",
        "get",
        "how",
        "i",
        "in",
        "is",
        "it",
        "me",
        "my",
        "of",
        "on",
        "or",
        "show",
        "the",
        "to",
        "what",
        "which",
        "with",
    ]
)
_MIN_STEM = 3

_NAME_WORD = 6
_NAME_SUBSTRING = 3
_DESCRIPTION_WORD = 2
_DESCRIPTION_SUBSTRING = 1
_CJK_PAIR = 2


def _stem(word: str) -> str:
    for suffix in ("ing", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= _MIN_STEM:
            return word[: -len(suffix)]
    return word


def _words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _cjk_pairs(text: str) -> set[str]:
    pairs: set[str] = set()
    for run in _CJK.findall(text):
        if len(run) == 1:
            pairs.add(run)
        pairs.update(run[i : i + 2] for i in range(len(run) - 1))
    return pairs


@dataclass(frozen=True)
class Candidate:
    name: str
    description: str


def score(intent: str, candidate: Candidate) -> int:
    terms = set(_words(intent.replace("_", " ")))
    name_words = set(_words(candidate.name.replace("_", " ")))
    description_words = set(_words(candidate.description))
    name_text = candidate.name.lower()
    description_text = candidate.description.lower()

    total = 0
    for term in terms:
        if term in name_words:
            total += _NAME_WORD
        elif term in name_text:
            total += _NAME_SUBSTRING
        if term in description_words:
            total += _DESCRIPTION_WORD
        elif term in description_text:
            total += _DESCRIPTION_SUBSTRING
    total += _CJK_PAIR * sum(1 for pair in _cjk_pairs(intent) if pair in candidate.description)
    return total


def rank(intent: str, candidates: Iterable[Candidate], *, limit: int) -> list[Candidate]:
    scored = [(score(intent, c), c) for c in candidates]
    hits = [(s, c) for s, c in scored if s > 0]
    hits.sort(key=lambda item: (-item[0], item[1].name))
    return [c for _s, c in hits[:limit]]


__all__ = ["Candidate", "rank", "score"]
