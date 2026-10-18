"""Pure rules: how a source is trusted, how evidence becomes a grade, when a row goes stale.

Grade (derived, never typed in for researched rows):
  high    >= 1 primary source, or >= 2 secondary sources from different publishers
  medium  1 secondary + >= 1 tertiary, or >= 2 tertiary from different publishers
  low     1 secondary, or tertiary sources only (shown with its low grade and its sources)
  none    no usable evidence at all -> the relation stays a candidate
Evidence that is rejected, or does not support the claim, is ignored.

Documented exception: a `seeded` relation carries no evidence and keeps its stated grade; it is
published as verified.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from urllib.parse import urlsplit

from analytics.supply_chain.domain.models import Evidence, Grade, Origin, Status, Trust

STALE_AFTER_MONTHS = 24
_MIN_INDEPENDENT = 2
_DECEMBER = 12

_PRIMARY = ("mops.twse.com.tw", "twse.com.tw", "tpex.org.tw", "sec.gov")
_SECONDARY = (
    "udn.com",
    "money.udn.com",
    "ctee.com.tw",
    "cnyes.com",
    "cna.com.tw",
    "reuters.com",
    "digitimes.com",
    "digitimes.com.tw",
    "ltn.com.tw",
    "bnext.com.tw",
    "businesstoday.com.tw",
    "setn.com",
    "chinatimes.com",
    "bloomberg.com",
    "wsj.com",
    "ft.com",
    "cnbc.com",
    "nikkei.com",
    "technews.tw",
)
_TERTIARY = (
    "moneydj.com",
    "histock.tw",
    "goodinfo.tw",
    "finlab.tw",
    "cmoney.tw",
    "wantgoo.com",
    "yahoo.com",
    "investing.com",
    "marketscreener.com",
)
_REJECTED = (
    "stockfeel.com.tw",
    "ptt.cc",
    "mobile01.com",
    "medium.com",
    "blogspot.com",
    "wordpress.com",
    "pixnet.net",
    "dcard.tw",
    "facebook.com",
    "reddit.com",
)

DEFAULT_TRUST_TABLE: Mapping[str, Trust] = {
    **dict.fromkeys(_TERTIARY, Trust.TERTIARY),
    **dict.fromkeys(_SECONDARY, Trust.SECONDARY),
    **dict.fromkeys(_PRIMARY, Trust.PRIMARY),
    **dict.fromkeys(_REJECTED, Trust.REJECTED),
}


def host_of(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def trust_for_url(url: str, trust_table: Mapping[str, Trust] | None = None) -> Trust:
    """Trust of a source by its domain. `trust_table` entries override the built-in table; the
    longest matching domain suffix wins. A domain not in the table is tertiary: a hostname that
    merely starts with `ir.` proves nothing (anyone can register one), so company investor-relations
    sites are primary only when listed in the trust table."""
    host = host_of(url)
    table = {**DEFAULT_TRUST_TABLE, **(trust_table or {})}
    matches = [d for d in table if host == d or host.endswith("." + d)]
    if matches:
        return table[max(matches, key=len)]
    return Trust.TERTIARY


_SECOND_LEVEL_LABELS = frozenset({"com", "co", "org", "net", "gov", "edu", "ac", "or", "ne"})
_CC_TLD_LENGTH = 2
_PLAIN_DOMAIN_LABELS = 2
_REGISTRABLE_LABELS_UNDER_SECOND_LEVEL = 3


def registrable_domain(host: str) -> str:
    """The site a host belongs to: `a.example.com` and `b.example.com` are one publisher. A
    deliberately small public-suffix rule (two labels, or three under `com.tw` / `co.jp` style
    suffixes) so that spinning up subdomains cannot fake independent sources."""
    labels = [part for part in host.lower().strip(".").split(".") if part]
    if len(labels) <= _PLAIN_DOMAIN_LABELS:
        return ".".join(labels)
    if len(labels[-1]) == _CC_TLD_LENGTH and labels[-2] in _SECOND_LEVEL_LABELS:
        return ".".join(labels[-_REGISTRABLE_LABELS_UNDER_SECOND_LEVEL:])
    return ".".join(labels[-_PLAIN_DOMAIN_LABELS:])


def independence_key(evidence: Evidence) -> str:
    """What makes two sources independent: the site they were published on."""
    host = host_of(evidence.url)
    return registrable_domain(host) if host else evidence.publisher.strip().lower()


def _publishers(evidence: Iterable[Evidence], trust: Trust) -> set[str]:
    return {independence_key(e) for e in evidence if e.trust is trust}


def grade_from_evidence(evidence: Iterable[Evidence]) -> Grade | None:
    """The grade the evidence supports, or None when it is too weak to publish."""
    usable = [e for e in evidence if e.supports and e.trust is not Trust.REJECTED]
    secondary = _publishers(usable, Trust.SECONDARY)
    tertiary = _publishers(usable, Trust.TERTIARY)
    if any(e.trust is Trust.PRIMARY for e in usable) or len(secondary) >= _MIN_INDEPENDENT:
        return Grade.HIGH
    if secondary:
        return Grade.MEDIUM if tertiary else Grade.LOW
    if len(tertiary) >= _MIN_INDEPENDENT:
        return Grade.MEDIUM
    return Grade.LOW if tertiary else None


def _months_before(today: date, months: int) -> date:
    year, month0 = divmod(today.year * 12 + today.month - 1 - months, 12)
    month = month0 + 1
    first_next = date(year + (month == _DECEMBER), month % _DECEMBER + 1, 1)
    last_day = (first_next - date(year, month, 1)).days
    return date(year, month, min(today.day, last_day))


def is_stale(as_of: date | None, last_verified: date | None, today: date) -> bool:
    """Older than 24 months by the newer of `as_of` / `last_verified`; undated is not stale."""
    dates = [d for d in (as_of, last_verified) if d is not None]
    if not dates:
        return False
    return max(dates) < _months_before(today, STALE_AFTER_MONTHS)


def resolve_status(
    *,
    declared: Status,
    origin: Origin,
    evidence_grade: Grade | None,
    stale: bool,
) -> Status:
    """The published status. A declared candidate/rejected stands; a verified claim must be
    earned by evidence (except `seeded`) and ages into stale."""
    if declared in (Status.CANDIDATE, Status.REJECTED):
        return declared
    if origin is not Origin.SEEDED and evidence_grade is None:
        return Status.CANDIDATE
    return Status.STALE if stale else Status.VERIFIED


__all__ = [
    "DEFAULT_TRUST_TABLE",
    "STALE_AFTER_MONTHS",
    "grade_from_evidence",
    "host_of",
    "independence_key",
    "is_stale",
    "registrable_domain",
    "resolve_status",
    "trust_for_url",
]
