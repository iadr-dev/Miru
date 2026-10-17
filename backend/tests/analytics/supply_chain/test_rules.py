from __future__ import annotations

from datetime import date

import pytest

from analytics.supply_chain.domain.models import Grade, Origin, Status, Trust
from analytics.supply_chain.domain.rules import (
    grade_from_evidence,
    is_stale,
    resolve_status,
    trust_for_url,
)
from tests.analytics.supply_chain.builders import evidence

P, S, T, R = Trust.PRIMARY, Trust.SECONDARY, Trust.TERTIARY, Trust.REJECTED


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([(P, "a")], Grade.HIGH),
        ([(S, "a"), (S, "b")], Grade.HIGH),
        ([(S, "a"), (S, "a")], Grade.LOW),  # same publisher twice is one source
        ([(S, "a")], Grade.LOW),
        ([(S, "a"), (T, "x")], Grade.MEDIUM),
        ([(T, "x"), (T, "y")], Grade.MEDIUM),
        ([(T, "x"), (T, "x")], None),
        ([(T, "x")], None),
        ([(R, "z"), (R, "w")], None),
        ([(S, "a"), (R, "z")], Grade.LOW),  # rejected evidence ignored
        ([], None),
    ],
)
def test_grade_table(rows: list[tuple[Trust, str]], expected: Grade | None) -> None:
    ev = [evidence(t, p, i) for i, (t, p) in enumerate(rows)]
    assert grade_from_evidence(ev) == expected


def test_non_supporting_evidence_is_ignored() -> None:
    weak = evidence(P).model_copy(update={"supports": False})
    assert grade_from_evidence([weak]) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://mops.twse.com.tw/mops/web/x", P),
        ("https://www.money.udn.com/a", S),
        ("https://news.cnyes.com/a", S),
        ("https://www.moneydj.com/a", T),
        ("https://www.ptt.cc/bbs/x", R),
        ("https://unknown.example.org/a", T),
        ("https://ir.somecompany.example/report", P),
    ],
)
def test_trust_for_url_default_table(url: str, expected: Trust) -> None:
    assert trust_for_url(url) == expected


def test_trust_table_overrides_default() -> None:
    assert trust_for_url("https://www.moneydj.com/a", {"moneydj.com": S}) == S
    assert trust_for_url("https://sub.corp.example/a", {"corp.example": P}) == P


@pytest.mark.parametrize(
    ("as_of", "verified", "today", "stale"),
    [
        (date(2024, 10, 17), None, date(2026, 10, 17), False),  # exactly 24 months
        (date(2024, 10, 16), None, date(2026, 10, 17), True),
        (date(2020, 1, 1), date(2026, 5, 1), date(2026, 10, 17), False),  # re-verified
        (None, None, date(2026, 10, 17), False),
        (date(2024, 2, 29), None, date(2026, 2, 28), False),  # leap-day clamp
        (date(2024, 2, 28), None, date(2026, 3, 31), True),
    ],
)
def test_staleness(as_of: date | None, verified: date | None, today: date, stale: bool) -> None:
    assert is_stale(as_of, verified, today) is stale


def test_resolve_status() -> None:
    def status(declared: Status, grade: Grade | None, stale: bool) -> Status:
        return resolve_status(
            declared=declared, origin=Origin.RESEARCH, evidence_grade=grade, stale=stale
        )

    assert status(Status.VERIFIED, Grade.LOW, False) is Status.VERIFIED
    assert status(Status.VERIFIED, None, False) is Status.CANDIDATE
    assert status(Status.VERIFIED, Grade.HIGH, True) is Status.STALE
    assert status(Status.REJECTED, Grade.HIGH, False) is Status.REJECTED


def test_seeded_is_verified_without_evidence() -> None:
    status = resolve_status(
        declared=Status.VERIFIED, origin=Origin.SEEDED, evidence_grade=None, stale=False
    )
    assert status is Status.VERIFIED
