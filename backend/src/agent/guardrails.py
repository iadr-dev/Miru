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

# Our own data-provider product names — governance-prompt rule 9: describe data by what it is
# ("structured market data"), never by whose product fetched it. Deliberately narrower than "any
# exchange/regulator name": TWSE/TPEx/TAIFEX/MOPS/TDCC are the real-world exchanges and official
# disclosure/depository systems themselves — pointing the user at "臺灣證券交易所官網" or 集保結算所
# for the authoritative source is encouraged, legitimate attribution, not a vendor leak. Only our
# own backend's licensed third-party data-provider choices (FinMind, yfinance, and yfinance's own
# upstream "Yahoo(-aggregated)" attribution in `sources/yfinance/_shared.py::_CAVEAT` — confirmed
# leaking verbatim into a `company_profile`/`analyst_consensus`/`esg_score` card's `sector_caveat`/
# `currentness_caveat` before this fix, since the pattern didn't cover it — which the user never
# asked about and has no reason to know) are in scope here.
_VENDOR_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(FinMind|yfinance|Yahoo(?:-aggregated)?)\b", re.IGNORECASE
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
    "A specific trading recommendation or price target was omitted here."
)


def _contains_trading_language(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern in _TRADING_PATTERNS)


_VENDOR_NAME_REPLACEMENT: Final[str] = "the data provider"


def _strip_vendor_names(text: str) -> tuple[str, bool]:
    """Substitute our own third-party data-provider product names with a generic phrase,
    in place — unlike trading-language redaction, this never discards the surrounding
    sentence (the vendor name is normally a single incidental word inside otherwise-fine
    prose, not the whole clause's reason for existing)."""
    cleaned, count = _VENDOR_NAME_PATTERN.subn(_VENDOR_NAME_REPLACEMENT, text)
    return cleaned, count > 0


def split_sentences(text: str) -> list[str]:
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


def scan_sentence(sentence: str) -> str:
    """Apply the same per-sentence trading-language/vendor-name scan `check_output` runs
    internally, to exactly one already-complete sentence — the seam the streaming answer path
    (`AgentLoop._stream_step`) calls as each sentence boundary closes, so a live token-by-token
    render never shows unscanned text: the sentence is redacted/cleaned before it is ever
    forwarded to a `message.delta` SSE frame, not after. Does not append the non-trading
    caveat — that is a whole-answer, end-of-stream concern `check_output` alone still owns, run
    once on the fully assembled text after streaming completes.

    `sentence` here comes from `split_sentences_streaming`, which (unlike `split_sentences`)
    keeps leading whitespace attached to preserve exact reconstruction of the streamed buffer —
    that whitespace is split off and reattached around the scan so a redacted sentence still
    reads "...prose. (redacted) More prose." with the space intact, not "...prose.(redacted)"."""
    stripped_len = len(sentence) - len(sentence.lstrip())
    prefix, content = sentence[:stripped_len], sentence[stripped_len:]
    if not content:
        return sentence
    if _contains_trading_language(content):
        return prefix + _REDACTION_NOTICE
    cleaned, _ = _strip_vendor_names(content)
    return prefix + cleaned


def split_sentences_streaming(buffer: str) -> list[str]:
    """Split `buffer` into sentence-boundary chunks WITHOUT stripping or discarding any
    character — `"".join(split_sentences_streaming(buffer)) == buffer` always holds. This is
    the incremental counterpart to `split_sentences` (used for the streaming answer path,
    `AgentLoop._stream_step`): that function calls `text.strip()` up front and consumes the
    whitespace between sentences, which is safe for a one-shot scan of a complete, final answer
    but corrupts a growing buffer — `text.strip()`-ing a still-incomplete tail mid-stream would
    permanently drop the word-boundary space the NEXT delta needs once it arrives (a real bug
    this function exists to avoid, not just a hypothetical edge case).

    The split point sits immediately after a terminator character, before any following
    whitespace — so that whitespace rides along with the START of the next chunk instead of
    being consumed by the split itself. A caller streaming this incrementally treats every
    chunk except the last as complete (nothing can still be appended to it) and holds the last
    chunk as the still-growing remainder, exactly like `AgentLoop._stream_step`'s own buffering
    loop does with `split_sentences`."""
    if not buffer:
        return []
    return re.split(r"(?<=[。！？])|(?<=[.!?])(?=\s)", buffer)


def check_output(text: str, *, has_market_data: bool = True) -> HookVerdict:
    """Scan a proposed final/sub-agent output for trading language and caveat presence.

    Runs on every candidate output — root synthesis and any future sub-agent lane alike.
    Redacts offending sentences in place (rather than discarding the whole answer) and
    appends the standard non-trading caveat when the text draws on market data but doesn't
    already carry one. Always `allowed=True`: the veto here is "what text reaches the
    caller", not "does the turn get to finish".
    """
    sentences = split_sentences(text)
    cleaned: list[str] = []
    violation = False
    vendor_name_stripped = False
    for sentence in sentences:
        if _contains_trading_language(sentence):
            violation = True
            # Collapse back-to-back violations into a single notice — several adjacent
            # offending sentences read as repetitive noise stacked one after another
            # (confirmed live: 3 separate copies of the same notice in one short answer)
            # rather than as N distinct redactions worth calling out individually.
            if not cleaned or cleaned[-1] != _REDACTION_NOTICE:
                cleaned.append(_REDACTION_NOTICE)
            continue
        stripped_sentence, sentence_had_vendor_name = _strip_vendor_names(sentence)
        vendor_name_stripped = vendor_name_stripped or sentence_had_vendor_name
        cleaned.append(stripped_sentence)

    # `split_sentences` discards each terminator's trailing whitespace, so a plain `" ".join`
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
    if violation:
        reason = "trading_language_redacted"
    elif vendor_name_stripped:
        reason = "vendor_name_redacted"
    else:
        reason = "caveat_appended"
    return HookVerdict(allowed=True, redacted_text=candidate, reason=reason)


# The standard non-trading caveat, exposed so a structured (non-prose) output — a rich card
# built from a typed tool result — can be stamped with the same caveat the prose path appends,
# rather than re-declaring the wording.
NON_TRADING_CAVEAT: Final[str] = _NON_TRADING_CAVEAT

# Exposed so callers (tests, any future caller wanting to detect a redaction) check against the
# real constant rather than hardcoding a substring of its wording, which would silently drift.
REDACTION_NOTICE: Final[str] = _REDACTION_NOTICE


def scan_structured_payload(value: object) -> tuple[object, bool]:
    """Redact trading language and strip our own data-provider vendor names from every
    text-bearing value in a structured payload, walking nested dicts/lists in place-of-value.

    The prose scan (`check_output`) works sentence-by-sentence on a single string; a structured
    card payload instead carries many short string values (labels, names, caveat text) among
    numeric/date fields, so the same trading-language patterns are applied to each string value
    (offending ones replaced with the redaction notice) and vendor-name substitution runs on
    every other string. Returns the cleaned value plus whether anything was redacted/substituted.
    The whole-payload caveat is stamped by the caller — there is no prose to append a sentence to
    here.
    """
    if isinstance(value, str):
        if _contains_trading_language(value):
            return _REDACTION_NOTICE, True
        return _strip_vendor_names(value)
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
    "REDACTION_NOTICE",
    "HookVerdict",
    "check_output",
    "check_tool_call",
    "scan_sentence",
    "scan_structured_payload",
    "split_sentences",
    "split_sentences_streaming",
]
