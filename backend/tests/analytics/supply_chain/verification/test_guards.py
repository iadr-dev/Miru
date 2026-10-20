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


def with_sentence(sentence: str, **overrides: object) -> Reason:
    return run(page_text=f"head\n{sentence}\ntail", excerpt=sentence, **overrides)


def test_a_clean_supply_claim_passes() -> None:
    assert run() is Reason.OK


def test_model_said_no() -> None:
    assert run(supports=False) is Reason.NOT_SUPPORTED


def test_excerpt_must_be_in_the_page() -> None:
    assert run(page_text="a page that says something else entirely") is Reason.NOT_IN_PAGE


def test_excerpt_match_ignores_whitespace_only() -> None:
    spaced = "阿爾法零件目前持續供貨給\n 貝塔裝置，為其主要供應商之一。"
    assert guards.excerpt_in_page(SENTENCE, spaced)
    assert not guards.excerpt_in_page(SENTENCE.replace("供貨", "供應商"), spaced)


def test_empty_and_long_excerpts_are_rejected() -> None:
    assert run(excerpt="  ") is Reason.NO_EXCERPT
    long_text = SENTENCE + "字" * 400
    assert run(page_text=long_text, excerpt=long_text) is Reason.EXCERPT_LONG


def test_both_company_names_must_be_in_the_excerpt() -> None:
    only_one = "阿爾法零件目前持續供貨給大型客戶。"
    assert with_sentence(only_one) is Reason.NAMES_MISSING
    assert with_sentence(SENTENCE) is Reason.OK


def test_english_aliases_and_corporate_suffix_are_matched() -> None:
    english = "Alpha Parts currently supplies chips to Beta Devices."
    assert with_sentence(english) is Reason.OK
    assert guards.mentions_any("alpha parts co., ltd. is listed", ALPHA.names)
    assert not guards.mentions_any("Delta Parts is listed", ALPHA.names)


@pytest.mark.parametrize(
    "sentence",
    [
        "阿爾法零件曾經供貨給貝塔裝置。",
        "阿爾法零件過去為貝塔裝置的供應商。",
        "阿爾法零件原為貝塔裝置供應商，合約已終止。",
        "Alpha Parts formerly supplied Beta Devices.",
        "Alpha Parts no longer supplies Beta Devices.",
        "歷年客戶包括貝塔裝置，阿爾法零件供貨。",
    ],
)
def test_past_or_ended_wording_is_rejected(sentence: str) -> None:
    assert with_sentence(sentence) is Reason.PAST_TENSE


def test_past_wording_is_allowed_when_the_same_sentence_is_present_tense() -> None:
    sentence = "阿爾法零件曾供貨給貝塔裝置，目前仍持續供貨。"
    assert with_sentence(sentence) is Reason.OK


def test_present_marker_in_another_sentence_does_not_rescue() -> None:
    sentence = "阿爾法零件曾供貨給貝塔裝置。目前市況良好。"
    assert with_sentence(sentence) is Reason.PAST_TENSE


@pytest.mark.parametrize(
    "sentence",
    [
        "阿爾法零件目前供貨給貝塔裝置與客戶A。",
        "阿爾法零件目前供貨給貝塔裝置及某大客戶。",
        "Alpha Parts currently supplies Beta Devices and a major customer.",
    ],
)
def test_anonymous_counterparties_are_rejected(sentence: str) -> None:
    assert with_sentence(sentence) is Reason.ANONYMOUS


def test_the_model_flag_for_anonymous_also_rejects() -> None:
    assert run(anonymous_counterparty=True) is Reason.ANONYMOUS


def test_named_customer_that_starts_with_a_letter_is_not_anonymous() -> None:
    assert not guards.is_anonymous("客戶Acme與阿爾法零件")
    assert guards.is_anonymous("客戶A與阿爾法零件")


@pytest.mark.parametrize(
    "sentence",
    [
        "阿爾法零件、貝塔裝置同業比較：供貨能力相近。",
        "阿爾法零件與貝塔裝置皆為AI概念股，供應鏈受惠。",
        "Peers include Alpha Parts and Beta Devices supplier names.",
    ],
)
def test_peer_and_concept_lists_are_rejected(sentence: str) -> None:
    assert with_sentence(sentence) is Reason.PEER_LIST


def test_a_named_customer_list_with_relation_wording_is_not_a_peer_list() -> None:
    sentence = "阿爾法零件目前主要客戶包括貝塔裝置、甲公司、乙公司、丙公司、丁公司。"
    assert not guards.is_peer_or_concept_list(sentence)
    assert with_sentence(sentence) is Reason.OK


def test_supply_claims_need_relation_wording() -> None:
    assert with_sentence("阿爾法零件與貝塔裝置同日發布財報。") is Reason.NO_RELATION


def test_competes_needs_explicit_rivalry_wording() -> None:
    no_rivalry = "阿爾法零件與貝塔裝置同日發布財報。"
    assert with_sentence(no_rivalry, kind=Kind.COMPETES) is Reason.NO_RIVALRY
    rivalry = "阿爾法零件目前為貝塔裝置的主要競爭對手。"
    assert with_sentence(rivalry, kind=Kind.COMPETES) is Reason.OK
    assert guards.has_rivalry_wording("A competes with B")
    assert not guards.has_rivalry_wording("A supplies B")


def test_direction_must_match_the_claim() -> None:
    assert run(direction="competes") is Reason.DIRECTION
    assert run(direction=None) is Reason.DIRECTION
    assert run(Kind.COMPETES, direction="supplies") is Reason.DIRECTION


def test_direction_flip_is_rejected() -> None:
    assert run(supplier_is_first=False) is Reason.FLIPPED


def test_not_current_is_rejected() -> None:
    assert run(current=False) is Reason.NOT_CURRENT


def test_old_pages_are_rejected_when_a_date_is_known() -> None:
    assert run(published=date(2020, 1, 1)) is Reason.SOURCE_OLD
    assert run(published=date(2026, 1, 1)) is Reason.OK
    assert run(published=None) is Reason.OK


def test_old_as_of_year_is_rejected() -> None:
    assert run(as_of_year=2019) is Reason.AS_OF_OLD


def test_rejected_hosts_are_rejected() -> None:
    table = {"news.example.com": Trust.REJECTED}
    assert run(trust_table=table) is Reason.HOST_REJECTED
    assert run(trust_table={"news.example.com": Trust.SECONDARY}) is Reason.OK


def test_advice_language_in_the_excerpt_is_rejected() -> None:
    sentence = "阿爾法零件目前供貨給貝塔裝置，you should buy Alpha Parts now."
    assert with_sentence(sentence) is Reason.ADVICE
    assert guards.has_advice_language("you should buy this stock")
    assert not guards.has_advice_language(SENTENCE)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025-03-04", date(2025, 3, 4)),
        ("發佈於 2025年3月4日", date(2025, 3, 4)),
        ("Mar 4, 2025", date(2025, 3, 4)),
        ("3 weeks ago", date(2026, 9, 27)),
        ("no date here", None),
        ("2025-13-45", None),
    ],
)
def test_loose_dates(text: str, expected: date | None) -> None:
    assert guards.parse_loose_date(text, today=TODAY) == expected


def test_page_date_prefers_the_hit_then_the_head_of_the_text() -> None:
    assert guards.page_date("2020-01-01 body", "2 days ago", TODAY) == date(2026, 10, 16)
    assert guards.page_date("2020-01-01 body", None, TODAY) == date(2020, 1, 1)
    assert guards.page_date("body only", None, TODAY) is None


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://news.example.com/2014/08/14/some-story", date(2014, 8, 14)),
        ("https://news.example.com/story/2025-03-04-title", date(2025, 3, 4)),
        ("https://news.example.com/n/20250304", date(2025, 3, 4)),
        ("https://news.example.com/n/20250304.html", date(2025, 3, 4)),
        # the shape of the live miss: /article/category/<n>/post/<YYYYMMDD><n>
        ("https://biz.example.com/article/category/80402/post/201408140", date(2014, 8, 14)),
        ("https://biz.example.com/post/2014081402", date(2014, 8, 14)),
    ],
)
def test_url_date_accepts_the_unambiguous_forms(url: str, expected: date) -> None:
    assert guards.url_date(url, TODAY) == expected


@pytest.mark.parametrize(
    "url",
    [
        None,
        "https://shop.example.com/order/12345678",
        "https://news.example.com/n/20141345",
        "https://news.example.com/n/19990101",
        "https://news.example.com/2014/13/14/x",
        "https://news.example.com/n/99999999",
        "https://news.example.com/item/201408140",
        "https://news.example.com/n/2014081412345",
        "https://news.example.com/s?date=20140814&d=2014-08-14",
        "https://news.example.com/n/20991231",
    ],
)
def test_url_date_ignores_ambiguous_or_invalid_digits(url: str | None) -> None:
    assert guards.url_date(url, TODAY) is None


def test_page_date_uses_the_url_only_as_a_last_resort() -> None:
    url = "https://news.example.com/2014/08/14/x"
    assert guards.page_date("body only", None, TODAY, url) == date(2014, 8, 14)
    assert guards.page_date("2024-02-02 body", None, TODAY, url) == date(2024, 2, 2)
    assert guards.page_date("body", "2 days ago", TODAY, url) == date(2026, 10, 16)
    assert guards.page_date("body only", None, TODAY) is None
