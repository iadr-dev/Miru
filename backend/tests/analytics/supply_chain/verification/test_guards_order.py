"""Characterization of `guards.evaluate`'s rule ORDER: when a judgement breaks several rules,
the reported reason is the first in the stable list, so rejection reasons stay comparable."""

from __future__ import annotations

from datetime import date

import pytest

from analytics.supply_chain.domain.models import Kind, Trust
from analytics.supply_chain.verification import guards
from analytics.supply_chain.verification.guards import Reason
from tests.analytics.supply_chain.verification.support import (
    ALPHA,
    BETA,
    PAGE_TEXT,
    SENTENCE,
    TODAY,
    judgement,
)

URL = "https://news.example.com/a"
OLD = date(2020, 1, 1)
RIVAL = "阿爾法零件目前為貝塔裝置的主要競爭對手。"


def run(
    kind: Kind = Kind.SUPPLIES,
    *,
    page_text: str = PAGE_TEXT,
    published: date | None = None,
    url: str = URL,
    trust_table: dict[str, Trust] | None = None,
    **overrides: object,
) -> Reason:
    return guards.evaluate(
        judgement(**({"direction": kind.value} | overrides)),
        kind=kind,
        supplier_names=ALPHA.names,
        customer_names=BETA.names,
        page_text=page_text,
        page_published=published,
        url=url,
        today=TODAY,
        max_age_days=1095,
        trust_table=trust_table,
    )


def sentence_run(sentence: str, kind: Kind = Kind.SUPPLIES, **overrides: object) -> Reason:
    return run(kind, page_text=f"head\n{sentence}\ntail", excerpt=sentence, **overrides)


REJECTED_HOST = {"news.example.com": Trust.REJECTED}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # supports is checked before everything, host rejection next
        ({"supports": False, "current": False}, Reason.NOT_SUPPORTED),
        ({"supports": False, "trust_table": REJECTED_HOST}, Reason.NOT_SUPPORTED),
        ({"trust_table": REJECTED_HOST, "direction": "competes"}, Reason.HOST_REJECTED),
        # direction before flip before current before anonymous flag before excerpt checks
        ({"direction": "competes", "supplier_is_first": False}, Reason.DIRECTION),
        ({"direction": None, "current": False}, Reason.DIRECTION),
        ({"supplier_is_first": False, "current": False}, Reason.FLIPPED),
        ({"current": False, "anonymous_counterparty": True}, Reason.NOT_CURRENT),
        ({"anonymous_counterparty": True, "excerpt": ""}, Reason.ANONYMOUS),
        ({"excerpt": "   "}, Reason.NO_EXCERPT),
        ({"excerpt": "不在頁面中的句子", "published": OLD}, Reason.NOT_IN_PAGE),
        # an old page and an old as-of year: the page date is reported first
        ({"published": OLD, "as_of_year": 2019}, Reason.SOURCE_OLD),
        ({"as_of_year": 2019}, Reason.AS_OF_OLD),
    ],
)
def test_the_first_broken_rule_is_the_one_reported(
    overrides: dict[str, object], expected: Reason
) -> None:
    assert run(**overrides) is expected


def test_flipped_applies_to_supplies_only() -> None:
    assert run(Kind.SUPPLIES, supplier_is_first=False) is Reason.FLIPPED
    assert sentence_run(RIVAL, Kind.COMPETES, supplier_is_first=False) is Reason.OK


def test_excerpt_is_stripped_before_it_is_checked() -> None:
    assert run(excerpt=f"  {SENTENCE}\n") is Reason.OK


def test_names_missing_beats_the_wording_rules() -> None:
    # no company named, past marker, peer list marker, and no relation wording all at once
    assert sentence_run("曾為同業的簡介。") is Reason.NAMES_MISSING


def test_excerpt_text_rules_run_in_order() -> None:
    both = "阿爾法零件與貝塔裝置"
    assert sentence_run(f"{both}的客戶 A 曾經採購。") is Reason.ANONYMOUS
    assert sentence_run(f"{both}過去供貨。") is Reason.PAST_TENSE
    assert sentence_run(f"{both}同業供貨。") is Reason.PEER_LIST
    assert sentence_run(f"{both}同日發布。") is Reason.NO_RELATION
    assert sentence_run(f"{both}同日發布。", Kind.COMPETES) is Reason.NO_RIVALRY


def test_past_marker_beats_peer_list_and_anonymous_beats_past() -> None:
    both = "阿爾法零件與貝塔裝置"
    assert sentence_run(f"{both}過去為同業供貨。") is Reason.PAST_TENSE
    assert sentence_run(f"{both}過去供貨給某客戶。") is Reason.ANONYMOUS


def test_source_age_is_checked_after_the_wording_rules_and_before_as_of_and_advice() -> None:
    advice = "阿爾法零件目前供貨給貝塔裝置，you should buy Alpha Parts now."
    assert sentence_run(advice) is Reason.ADVICE
    assert sentence_run(advice, published=OLD) is Reason.SOURCE_OLD
    assert sentence_run(advice, as_of_year=2019) is Reason.AS_OF_OLD
    assert sentence_run("阿爾法零件與貝塔裝置同日發布。", published=OLD) is Reason.NO_RELATION


def test_competes_passes_with_rivalry_wording_and_needs_no_relation_wording() -> None:
    assert sentence_run(RIVAL, Kind.COMPETES) is Reason.OK
    assert not guards.has_relation_wording(RIVAL)


def test_as_of_year_boundary_is_the_cutoff_year_inclusive() -> None:
    # today 2026-10-18 minus 1095 days falls in 2023
    assert run(as_of_year=2023) is Reason.OK
    assert run(as_of_year=2022) is Reason.AS_OF_OLD
    assert run(as_of_year=None) is Reason.OK


def test_source_age_boundary_is_strictly_greater_than_the_limit() -> None:
    edge = date.fromordinal(TODAY.toordinal() - 1095)
    assert run(published=edge) is Reason.OK
    assert run(published=date.fromordinal(edge.toordinal() - 1)) is Reason.SOURCE_OLD
