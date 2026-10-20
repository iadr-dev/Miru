"""Deterministic guards on every model judgement. Pure functions, no I/O.

The model proposes; these rules decide. Nothing here reads the model's own opinion of its answer
beyond the typed fields, and nothing the page says can change a rule: a page that instructs the
model gains nothing, because the excerpt must literally be in the page, name both companies, and
read as a present, named, explicit relationship.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date, timedelta
from enum import StrEnum
from typing import Final
from urllib.parse import urlsplit

from analytics.supply_chain.domain.models import EXCERPT_MAX, Kind, Trust
from analytics.supply_chain.domain.rules import trust_for_url
from analytics.supply_chain.verification.models import Judgement
from governance.textscan import check_output


class Reason(StrEnum):
    OK = "ok"
    NOT_SUPPORTED = "model_not_supported"
    DIRECTION = "direction_mismatch"
    FLIPPED = "direction_flipped"
    NOT_CURRENT = "not_current"
    ANONYMOUS = "anonymous_counterparty"
    NO_EXCERPT = "no_excerpt"
    EXCERPT_LONG = "excerpt_too_long"
    NOT_IN_PAGE = "excerpt_not_in_page"
    NAMES_MISSING = "names_missing"
    PAST_TENSE = "past_or_ended"
    PEER_LIST = "peer_or_concept_list"
    NO_RELATION = "no_relation_wording"
    NO_RIVALRY = "no_rivalry_wording"
    SOURCE_OLD = "source_too_old"
    AS_OF_OLD = "as_of_too_old"
    HOST_REJECTED = "host_rejected"
    ADVICE = "advice_language"


_PAST_MARKERS: Final = (
    "曾",
    "過去",
    "原為",
    "原先",
    "先前",
    "已終止",
    "已停止",
    "歷年",
    "formerly",
    "previously",
    "no longer",
    "used to",
    "was once",
)
_PRESENT_MARKERS: Final = (
    "目前",
    "現為",
    "現在",
    "現行",
    "迄今",
    "持續",
    "仍",
    "依舊",
    "currently",
    "remains",
    "remain ",
    "continues",
    "continue to",
    "still",
    "ongoing",
)
_ANONYMOUS: Final = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"客戶\s*[A-Za-z](?![A-Za-z])",
        r"客戶\s*[甲乙丙丁]",
        r"某(?:大|一|國際|知名|海外|國外)?(?:客戶|大廠|廠商|業者|企業|公司)",
        r"\b(?:a|one)\s+(?:major|large|key|leading|big)\s+(?:customer|client)\b",
        r"\b(?:an?\s+)?(?:unnamed|undisclosed|unidentified)\s+(?:customer|client)\b",
        r"\bcustomer\s+[A-D]\b",
    )
)
_LIST_MARKERS: Final = (
    "同業",
    "概念股",
    "受惠股",
    "相關個股",
    "相關股",
    "類股",
    "成分股",
    "題材股",
    "peer group",
    "peers",
    "peer comparison",
    "concept stock",
    "related stocks",
    "stocks to watch",
    "beneficiaries",
)
_RELATION_WORDS: Final = (
    "供應",
    "供貨",
    "客戶",
    "出貨",
    "訂單",
    "採購",
    "代工",
    "委託",
    "導入",
    "打入",
    "採用",
    "supplier",
    "supplies",
    "supplying",
    "supplied",
    "supply to",
    "customer",
    "client",
    "shipments to",
    "orders from",
    "sells to",
    "sold to",
    "provides",
    "provided",
)
_RIVALRY_WORDS: Final = (
    "競爭",
    "對手",
    "對抗",
    "較勁",
    "rival",
    "compete",
    "competes",
    "competing",
    "competitor",
    "competitors",
)
_ENUMERATION_LIMIT: Final = 4
_SENTENCE_SPLIT: Final = re.compile(r"(?<=[。！？!?；;])|\n|(?<=\.)\s+")
_WHITESPACE: Final = re.compile(r"\s+")
_COMPANY_SUFFIX: Final = re.compile(
    r"[\s,.]+(?:inc|corp|corporation|co|company|ltd|limited|plc|llc|holdings?|group)\.?$",
    re.IGNORECASE,
)
_MIN_STRIPPED_NAME = 4

_ISO = re.compile(r"(20\d{2}|19\d{2})[-/.](\d{1,2})[-/.](\d{1,2})")
_CJK_DATE = re.compile(r"(20\d{2}|19\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_EN_DATE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+(\d{1,2}),?\s+"
    r"(20\d{2}|19\d{2})\b",
    re.IGNORECASE,
)
_RELATIVE = re.compile(r"(\d+)\s*(day|week|month|year)s?\s+ago", re.IGNORECASE)
_MONTHS: Final = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_DAYS_PER: Final = {"day": 1, "week": 7, "month": 30, "year": 365}
_HEAD_CHARS = 1500


def squash(text: str) -> str:
    """Whitespace-free form used to compare text (page extraction adds and drops spaces freely)."""
    return _WHITESPACE.sub("", text.replace("　", " "))


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_loose_date(text: str | None, *, today: date | None = None) -> date | None:
    """The first full date in `text` (ISO, 2025年3月4日, `Mar 4, 2025`, or `3 weeks ago`)."""
    if not text:
        return None
    for pattern in (_CJK_DATE, _ISO):
        if m := pattern.search(text):
            return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    if m := _EN_DATE.search(text):
        return _safe_date(int(m.group(3)), _MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
    if today is not None and (m := _RELATIVE.search(text)):
        return today - timedelta(days=int(m.group(1)) * _DAYS_PER[m.group(2).lower()])
    return None


_URL_SLASHED = re.compile(r"(?<!\d)(\d{4})/(\d{2})/(\d{2})(?!\d)")
_URL_DASHED = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_URL_COMPACT = re.compile(r"(\d{4})(\d{2})(\d{2})(?:\.html?)?")
_URL_ID = re.compile(r"(\d{4})(\d{2})(\d{2})\d{1,4}")
_URL_ID_PARENTS: Final = frozenset({"post", "posts", "article", "articles", "news", "story", "id"})
_URL_MIN_YEAR = 2000


def _url_candidate(year: str, month: str, day: str, today: date) -> date | None:
    y = int(year)
    if not _URL_MIN_YEAR <= y <= today.year:
        return None
    found = _safe_date(y, int(month), int(day))
    return found if found is not None and found <= today else None


def url_date(url: str | None, today: date) -> date | None:
    """A publication date embedded in the URL path, only in unambiguous forms: /YYYY/MM/DD/,
    YYYY-MM-DD, a bare /YYYYMMDD segment, or a 9-12 digit id (date + a short sequence) after a
    post/article-like segment whose first eight digits are a valid date. Query strings, other
    numeric ids and out-of-range
    years (before 2000, after today) yield None."""
    if not url:
        return None
    path = urlsplit(url).path
    for pattern in (_URL_SLASHED, _URL_DASHED):
        if m := pattern.search(path):
            return _url_candidate(m.group(1), m.group(2), m.group(3), today)
    segments = [seg for seg in path.split("/") if seg]
    for i, seg in enumerate(segments):
        parent = segments[i - 1].casefold() if i else ""
        m = _URL_COMPACT.fullmatch(seg) or (
            _URL_ID.fullmatch(seg) if parent in _URL_ID_PARENTS else None
        )
        if m and (found := _url_candidate(m.group(1), m.group(2), m.group(3), today)):
            return found
    return None


def page_date(
    text: str, hit_published: str | None, today: date, url: str | None = None
) -> date | None:
    """The page's date: the search hit's own, else the first full date near the top of the text,
    else (last resort, never overriding those) an unambiguous date in the URL path."""
    return (
        parse_loose_date(hit_published, today=today)
        or parse_loose_date(text[:_HEAD_CHARS])
        or url_date(url, today)
    )


def _strip_suffixes(name: str) -> str:
    current = name.strip().rstrip(" ,.")
    while (stripped := _COMPANY_SUFFIX.sub("", current).rstrip(" ,.")) != current:
        current = stripped
    return current


def name_variants(names: Iterable[str]) -> list[str]:
    """Squashed, case-folded names plus the corporate-suffix-stripped form of each."""
    out: list[str] = []
    for raw in names:
        full = squash(raw).casefold()
        if not full:
            continue
        out.append(full)
        stripped = squash(_strip_suffixes(raw)).casefold()
        if stripped != full and len(stripped) >= _MIN_STRIPPED_NAME:
            out.append(stripped)
    return out


def mentions_any(text: str, names: Iterable[str]) -> bool:
    haystack = squash(text).casefold()
    return any(v in haystack for v in name_variants(names))


def excerpt_in_page(excerpt: str, page_text: str) -> bool:
    needle = squash(excerpt)
    return bool(needle) and needle in squash(page_text)


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT.split(text) if s and s.strip()]


def _has_any(text: str, words: Iterable[str]) -> bool:
    lowered = text.casefold()
    return any(w.casefold() in lowered for w in words)


def has_unqualified_past_marker(excerpt: str) -> bool:
    """A past/ended marker in a sentence with no present-tense marker."""
    return any(
        _has_any(s, _PAST_MARKERS) and not _has_any(s, _PRESENT_MARKERS)
        for s in _sentences(excerpt)
    )


def is_anonymous(excerpt: str) -> bool:
    return any(p.search(excerpt) for p in _ANONYMOUS)


def has_relation_wording(excerpt: str) -> bool:
    return _has_any(excerpt, _RELATION_WORDS)


def has_rivalry_wording(excerpt: str) -> bool:
    return _has_any(excerpt, _RIVALRY_WORDS)


def is_peer_or_concept_list(excerpt: str) -> bool:
    """A sector, peer or concept-stock enumeration rather than a stated relationship."""
    if _has_any(excerpt, _LIST_MARKERS):
        return True
    separators = sum(excerpt.count(c) for c in ("、", "/", "|"))
    return separators >= _ENUMERATION_LIMIT and not (
        has_relation_wording(excerpt) or has_rivalry_wording(excerpt)
    )


def source_too_old(published: date | None, today: date, max_age_days: int) -> bool:
    return published is not None and (today - published).days > max_age_days


def has_advice_language(text: str) -> bool:
    """True when the governance scan would redact or rewrite the text (advice wording, or a data
    vendor's name)."""
    return check_output(text, has_market_data=False).redacted_text is not None


def evaluate(  # noqa: PLR0913, PLR0911, PLR0912 - one flat, ordered rule list is the point
    judgement: Judgement,
    *,
    kind: Kind,
    supplier_names: Iterable[str],
    customer_names: Iterable[str],
    page_text: str,
    page_published: date | None,
    url: str,
    today: date,
    max_age_days: int,
    trust_table: Mapping[str, Trust] | None = None,
) -> Reason:
    """The first rule the judgement breaks, or `Reason.OK`. The order is stable (cheapest and most
    decisive first) so rejection reasons are comparable across runs."""
    if not judgement.supports:
        return Reason.NOT_SUPPORTED
    if trust_for_url(url, trust_table) is Trust.REJECTED:
        return Reason.HOST_REJECTED
    if judgement.direction is None or judgement.direction != kind.value:
        return Reason.DIRECTION
    if kind is Kind.SUPPLIES and not judgement.supplier_is_first:
        return Reason.FLIPPED
    if not judgement.current:
        return Reason.NOT_CURRENT
    if judgement.anonymous_counterparty:
        return Reason.ANONYMOUS
    excerpt = judgement.excerpt.strip()
    if not excerpt:
        return Reason.NO_EXCERPT
    if len(excerpt) > EXCERPT_MAX:
        return Reason.EXCERPT_LONG
    if not excerpt_in_page(excerpt, page_text):
        return Reason.NOT_IN_PAGE
    if not (mentions_any(excerpt, supplier_names) and mentions_any(excerpt, customer_names)):
        return Reason.NAMES_MISSING
    if is_anonymous(excerpt):
        return Reason.ANONYMOUS
    if has_unqualified_past_marker(excerpt):
        return Reason.PAST_TENSE
    if is_peer_or_concept_list(excerpt):
        return Reason.PEER_LIST
    if kind is Kind.SUPPLIES and not has_relation_wording(excerpt):
        return Reason.NO_RELATION
    if kind is Kind.COMPETES and not has_rivalry_wording(excerpt):
        return Reason.NO_RIVALRY
    if source_too_old(page_published, today, max_age_days):
        return Reason.SOURCE_OLD
    if (
        judgement.as_of_year is not None
        and judgement.as_of_year < (today - timedelta(days=max_age_days)).year
    ):
        return Reason.AS_OF_OLD
    if has_advice_language(excerpt):
        return Reason.ADVICE
    return Reason.OK


__all__ = [
    "Reason",
    "evaluate",
    "excerpt_in_page",
    "has_advice_language",
    "has_relation_wording",
    "has_rivalry_wording",
    "has_unqualified_past_marker",
    "is_anonymous",
    "is_peer_or_concept_list",
    "mentions_any",
    "name_variants",
    "page_date",
    "parse_loose_date",
    "source_too_old",
    "squash",
    "url_date",
]
