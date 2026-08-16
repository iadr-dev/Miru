"""The non-negotiable governance hook: `pre_tool` (no full-market scan) and `pre_output`
(no trading language, caveats present).

This is a *separate* concern from the `sponsor` data-class entitlement gate (`tools.base.
dispatch_tool`) — that gate substitutes a masked teaser for unentitled paid-data calls;
this module never looks at tiers or billing at all. Both checks run on every relevant call,
independently, and neither can stand in for the other.

Detection mechanism (a deliberate first pass, not NLP): keyword/pattern matching over the
proposed tool arguments and the proposed output text. The trading-language patterns are
scoped to *advice-shaped* phrasing (imperative buy/sell/hold, ratings, price targets,
position sizing) rather than bare verbs, because market-context prose routinely uses "bought"/
"sold" to describe institutional flows that already happened — flagging every occurrence of
those words would make the loop unusable. A later hardening pass may replace or extend these
patterns; the call sites (`check_tool_call`/`check_output`) are the stable contract other
modules depend on.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Final, cast

from pydantic import BaseModel, ConfigDict


class HookVerdict(BaseModel):
    """The outcome of a `pre_tool`/`pre_output` check.

    `allowed=False` is a hard veto (used only for the full-market-scan tool-argument check —
    there is no legitimate redaction of a scan request, it is simply rejected). Output checks
    never hard-block; they redact/append instead so the caller always has *something* safe to
    show, per the "block or redact" wording of the rule this enforces.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    allowed: bool
    redacted_text: str | None = None
    reason: str | None = None


# ---- pre_tool: no full-market scan ----

_FULL_MARKET_SCAN_KEYS: Final[tuple[str, ...]] = (
    "scan_all",
    "full_market",
    "whole_market",
    "market_wide",
    "all_symbols",
)
_FULL_MARKET_SCAN_VALUES: Final[frozenset[str]] = frozenset({"*", "all", "all_symbols", "market"})
_MAX_SYMBOLS_PER_CALL: Final[int] = 20


def check_tool_call(tool_name: str, arguments: Mapping[str, object]) -> HookVerdict:
    """Reject tool arguments that request the whole market.

    Every immediate/deferred tool built so far already requires a single `(symbol, market)`
    pair, so this should rarely trigger in practice — it exists as the enforced seam itself,
    not as a substitute for narrow tool schemas.
    """
    for key, value in arguments.items():
        lowered_key = key.lower()
        if any(marker in lowered_key for marker in _FULL_MARKET_SCAN_KEYS):
            return HookVerdict(
                allowed=False,
                reason=f"{tool_name}: argument {key!r} requests a full-market scan",
            )
        if isinstance(value, str) and value.strip().lower() in _FULL_MARKET_SCAN_VALUES:
            return HookVerdict(
                allowed=False,
                reason=f"{tool_name}: {key!r}={value!r} requests a full-market scan",
            )
        if isinstance(value, list) and len(cast("list[object]", value)) > _MAX_SYMBOLS_PER_CALL:
            return HookVerdict(
                allowed=False,
                reason=f"{tool_name}: {key!r} names too many symbols for one bounded call",
            )
    return HookVerdict(allowed=True)


# ---- pre_output: non-trading + caveat enforcement ----

_TRADING_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\byou should (buy|sell|hold)\b",
        r"\bwe recommend (buying|selling|holding)\b",
        r"\bi recommend (buying|selling|holding)\b",
        # "signal" deliberately excluded from this group — "buy/sell signal" is common,
        # legitimate descriptive institutional-flow/technical-analysis terminology (e.g. "this
        # net-buy reads as a bullish signal"), unlike "rating"/"recommendation"/"call", which are
        # specifically advice-shaped and essentially never appear in neutral descriptive prose. A
        # genuinely advice-shaped "signal" sentence ("you should buy on this signal") is still
        # caught by the separate "you should buy/sell/hold" pattern above.
        r"\b(buy|sell|hold)\s+(rating|recommendation|call)\b",
        r"\b(buy|sell)\s+now\b",
        r"\bstrong (buy|sell)\b",
        r"\bprice target\b",
        r"\btarget price\b",
        r"\bPT of\b",
        r"\bposition siz(e|ing)\b",
        r"\ballocate\s+\d+%",
        r"\bhow many shares (should|to) (buy|sell)\b",
        r"目標價",
        r"買進建議",
        r"賣出建議",
    )
)

_CAVEAT_MARKERS: Final[tuple[str, ...]] = (
    "not financial advice",
    "not investment advice",
    "non-trading",
    "not a recommendation",
    "not real-time",
    "not guaranteed real-time",
    "currentness",
    "informational purposes only",
)

_NON_TRADING_CAVEAT: Final[str] = (
    "This is analytical market context only — not a buy/sell/hold recommendation, and no "
    "source here is guaranteed real-time."
)

_REDACTION_NOTICE: Final[str] = (
    "(此處內容因涉及買賣建議已省略 / content omitted — not a trading recommendation)"
)


def _contains_trading_language(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _TRADING_PATTERNS)


def _split_sentences(text: str) -> list[str]:
    """Split on both ASCII (`. ! ?`) and full-width Chinese (`。！？`) sentence terminators.

    Chinese sentences don't reliably have trailing whitespace after the terminator (unlike
    English), so the split keeps the terminator attached without requiring `\\s+` after it —
    an ASCII-only split treats an entire Chinese-language answer as one "sentence", so a single
    stray trading-language match anywhere in it would redact the whole multi-paragraph answer
    instead of just the offending clause.

    An ASCII terminator only splits when followed by whitespace (`\\s+`, not `\\s*`) — a bare
    `.` immediately followed by a non-space character never splits. Without this, a URL/domain
    (`https://www.tsmc.com`) or a decimal number (`12.5%`) gets chopped at every `.` and the
    caller's `" ".join`-with-spaces rejoin (see `check_output`) corrupts it into
    `https://www. tsmc. com` — a real bug this guards against, not just an edge case. Full-width
    terminators are unaffected: Chinese prose has no space to require, so they keep the original
    zero-width split.
    """
    if not text:
        return []
    parts = re.split(r"(?<=[。！？])\s*|(?<=[.!?])\s+", text.strip())
    return [part for part in parts if part]


def _has_caveat(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CAVEAT_MARKERS)


def check_output(text: str, *, has_market_data: bool = True) -> HookVerdict:
    """Scan a proposed final/sub-agent output for trading language and caveat presence.

    Runs on every candidate output — root synthesis and any future sub-agent lane alike.
    Redacts offending sentences in place (rather than discarding the whole answer) and
    appends the standard non-trading caveat when the text draws on market data but doesn't
    already carry one. Always `allowed=True`: the veto here is "what text reaches the
    caller", not "does the turn get to finish".
    """
    sentences = _split_sentences(text)
    cleaned: list[str] = []
    violation = False
    for sentence in sentences:
        if _contains_trading_language(sentence):
            violation = True
            cleaned.append(_REDACTION_NOTICE)
        else:
            cleaned.append(sentence)

    # `_split_sentences` discards each terminator's trailing whitespace, so a plain `" ".join`
    # would insert a space that was never there for CJK text (Chinese sentences run together
    # with no space after `。！？`) while still needing one between ASCII sentences (`. ! ?`).
    # Rejoin using each *preceding* sentence's own terminator to decide.
    candidate = cleaned[0] if cleaned else ""
    for sentence in cleaned[1:]:
        separator = "" if candidate and candidate[-1] in "。！？" else " "
        candidate += separator + sentence
    candidate = candidate.strip()
    if has_market_data and not _has_caveat(candidate):
        candidate = f"{candidate}\n\n{_NON_TRADING_CAVEAT}".strip()

    if candidate == text.strip():
        return HookVerdict(allowed=True)
    reason = "trading_language_redacted" if violation else "caveat_appended"
    return HookVerdict(allowed=True, redacted_text=candidate, reason=reason)


# The standard non-trading caveat, exposed so a structured (non-prose) output — a rich card
# built from a typed tool result — can be stamped with the same caveat the prose path appends,
# rather than re-declaring the wording.
NON_TRADING_CAVEAT: Final[str] = _NON_TRADING_CAVEAT

# Exposed so callers (tests, any future caller wanting to detect a redaction) check against the
# real constant rather than hardcoding a substring of its wording, which would silently drift.
REDACTION_NOTICE: Final[str] = _REDACTION_NOTICE


def scan_structured_payload(value: object) -> tuple[object, bool]:
    """Redact trading language from every text-bearing value in a structured payload, walking
    nested dicts/lists in place-of-value.

    The prose scan (`check_output`) works sentence-by-sentence on a single string; a structured
    card payload instead carries many short string values (labels, names, caveat text) among
    numeric/date fields, so the same trading-language patterns are applied to each string value
    and an offending one is replaced with the redaction notice. Returns the cleaned value plus
    whether anything was redacted. The whole-payload caveat is stamped by the caller — there is
    no prose to append a sentence to here.
    """
    if isinstance(value, str):
        if _contains_trading_language(value):
            return _REDACTION_NOTICE, True
        return value, False
    if isinstance(value, Mapping):
        mapping = cast("Mapping[object, object]", value)
        cleaned_map: dict[str, object] = {}
        redacted = False
        for key, item in mapping.items():
            new_item, item_redacted = scan_structured_payload(item)
            cleaned_map[str(key)] = new_item
            redacted = redacted or item_redacted
        return cleaned_map, redacted
    if isinstance(value, list | tuple):
        sequence = cast("list[object] | tuple[object, ...]", value)
        cleaned_list: list[object] = []
        redacted = False
        for item in sequence:
            new_item, item_redacted = scan_structured_payload(item)
            cleaned_list.append(new_item)
            redacted = redacted or item_redacted
        return cleaned_list, redacted
    return value, False


__all__ = [
    "NON_TRADING_CAVEAT",
    "HookVerdict",
    "check_output",
    "check_tool_call",
    "scan_structured_payload",
]
