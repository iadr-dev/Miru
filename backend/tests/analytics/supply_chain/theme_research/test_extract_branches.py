"""Characterization of `claims_from_reply`: every drop reason and its precedence, the item cap,
symbol fallback resolution and the position normalisation. Synthetic companies only."""

from __future__ import annotations

import json

import pytest

from analytics.supply_chain.domain.concepts import Entity, EntityType
from analytics.supply_chain.theme_research.extract import (
    DROP_ALREADY_MEMBER,
    DROP_BAD_ITEM,
    DROP_NOT_IN_PAGE,
    DROP_NOT_LISTED,
    DROP_UNRESOLVED,
    MAX_ITEMS_PER_PAGE,
    PageClaims,
    claims_from_reply,
)
from analytics.supply_chain.theme_research.models import ThemeTarget
from analytics.supply_chain.verification.directory import build_directory
from analytics.supply_chain.verification.models import Page
from tests.analytics.supply_chain.theme_research.support import COMPANIES, THEME_KEY, THEME_ZH

ZETA = COMPANIES["9101"][0]
ETA = COMPANIES["9102"][0]
OMICRON = COMPANIES["9100"][0]
LINE_ZETA = f"{ZETA}是{THEME_ZH}的重要供應商。"
LINE_ETA = f"{ETA}已切入{THEME_ZH}供應鏈。"
LINE_OMICRON = f"{OMICRON}是{THEME_ZH}的上游。"
LINE_US = f"Gamma Corp 是{THEME_ZH}的客戶。"
PAGE = Page(
    url="https://news.example.com/theme",
    title="t",
    text=f"head\n{LINE_ZETA}\n{LINE_ETA}\n{LINE_OMICRON}\n{LINE_US}\n",
)
TARGET = ThemeTarget(
    target_id=THEME_KEY,
    kind="existing",
    label=THEME_ZH,
    names=(THEME_ZH,),
    members=frozenset({"9100"}),
)


def _directory():
    roster = {s: zh for s, (zh, _) in COMPANIES.items()}
    entities = [
        Entity(id=zh, type=EntityType.TW_COMPANY, symbol=s, market="TW", aliases=(en,))
        for s, (zh, en) in COMPANIES.items()
    ]
    entities.append(
        Entity(id="Gamma Corp", type=EntityType.INTL_COMPANY, symbol="ZZZA", market="US")
    )
    return build_directory(roster=roster, market_values={}, entities=entities)


def convert(
    reply: str, target: ThemeTarget = TARGET, published_on: str | None = "2026-09-01"
) -> PageClaims:
    return claims_from_reply(
        reply,
        target=target,
        page=PAGE,
        directory=_directory(),
        retrieved_at="2026-10-18",
        published_on=published_on,
        publisher="news.example.com",
    )


def item(company: object, excerpt: object, **kw: object) -> dict[str, object]:
    return {"company": company, "symbol": None, "excerpt": excerpt, "position": "midstream"} | kw


def reply(*items: object) -> str:
    return json.dumps(list(items), ensure_ascii=False)


def test_a_reply_that_is_not_a_json_array_is_unparsed() -> None:
    for text in ("", "no json", '{"company": "x"}', '"text"'):
        out = convert(text)
        assert (out.claims, dict(out.dropped), out.parsed) == ([], {}, False)


def test_an_empty_array_is_parsed_with_nothing_dropped() -> None:
    out = convert("[]")
    assert (out.claims, dict(out.dropped), out.parsed) == ([], {}, True)


def test_a_good_item_becomes_a_claim_with_every_field_filled() -> None:
    out = convert(reply(item(ZETA, LINE_ZETA, position="UPSTREAM ")))
    [claim] = out.claims
    assert out.parsed
    assert dict(out.dropped) == {}
    assert claim.theme == THEME_KEY
    assert claim.kind == "existing"
    assert claim.label == THEME_ZH
    assert claim.symbol == "9101"
    assert claim.excerpt == LINE_ZETA
    assert claim.url == PAGE.url
    assert claim.publisher == "news.example.com"
    assert claim.retrieved_at == "2026-10-18"
    assert claim.position == "upstream"
    assert claim.published_on == "2026-09-01"


def test_published_on_may_be_missing_and_text_is_stripped() -> None:
    out = convert(reply(item(f"  {ZETA} ", f"  {LINE_ZETA}\n")), published_on=None)
    [claim] = out.claims
    assert claim.published_on is None
    assert claim.excerpt == LINE_ZETA


@pytest.mark.parametrize("raw", ["x", 3, None, ["a"]])
def test_non_object_items_are_bad_items(raw: object) -> None:
    out = convert(reply(raw))
    assert out.claims == []
    assert dict(out.dropped) == {DROP_BAD_ITEM: 1}


@pytest.mark.parametrize(
    "bad",
    [
        item("", LINE_ZETA),
        item(ZETA, ""),
        item(None, LINE_ZETA),
        item(ZETA, None),
        item("   ", "   "),
        {"excerpt": LINE_ZETA},
    ],
)
def test_items_missing_a_company_or_excerpt_are_bad_items(bad: dict[str, object]) -> None:
    assert dict(convert(reply(bad)).dropped) == {DROP_BAD_ITEM: 1}


def test_unknown_names_are_unresolved_unless_the_stated_code_resolves() -> None:
    assert dict(convert(reply(item("查無此公司", LINE_ZETA))).dropped) == {DROP_UNRESOLVED: 1}
    assert dict(convert(reply(item("查無此公司", LINE_ZETA, symbol="9999"))).dropped) == {
        DROP_UNRESOLVED: 1
    }
    out = convert(reply(item("查無此公司", LINE_ZETA, symbol=" 9101 ")))
    assert [c.symbol for c in out.claims] == ["9101"]
    assert dict(out.dropped) == {}


def test_the_name_wins_over_a_conflicting_code() -> None:
    out = convert(reply(item(ZETA, LINE_ZETA, symbol="9102")))
    assert [c.symbol for c in out.claims] == ["9101"]


def test_a_code_is_looked_up_in_tw_only() -> None:
    out = convert(reply(item("查無此公司", LINE_US, symbol="ZZZA")))
    assert dict(out.dropped) == {DROP_UNRESOLVED: 1}


def test_companies_outside_taiwan_are_not_listed() -> None:
    out = convert(reply(item("Gamma Corp", LINE_US)))
    assert out.claims == []
    assert dict(out.dropped) == {DROP_NOT_LISTED: 1}


def test_existing_members_are_dropped_before_the_page_check() -> None:
    out = convert(reply(item(OMICRON, "not on the page")))
    assert dict(out.dropped) == {DROP_ALREADY_MEMBER: 1}
    assert out.claims == []


def test_an_excerpt_not_in_the_page_is_dropped_last() -> None:
    out = convert(reply(item(ZETA, "這句話不在頁面上")))
    assert dict(out.dropped) == {DROP_NOT_IN_PAGE: 1}


def test_excerpt_match_ignores_whitespace() -> None:
    spaced = LINE_ZETA.replace("是", " 是 ")
    assert len(convert(reply(item(ZETA, spaced))).claims) == 1


def test_unknown_or_missing_positions_default_to_related() -> None:
    out = convert(
        reply(item(ZETA, LINE_ZETA, position="center"), item(ETA, LINE_ETA, position=None))
    )
    assert [c.position for c in out.claims] == ["related", "related"]
    missing = item(ZETA, LINE_ZETA)
    del missing["position"]
    assert [c.position for c in convert(reply(missing)).claims] == ["related"]


def test_mixed_items_keep_order_and_count_each_drop() -> None:
    out = convert(
        reply(
            item(ZETA, LINE_ZETA),
            "junk",
            item("查無此公司", LINE_ZETA),
            item(ETA, LINE_ETA),
            item("Gamma Corp", LINE_US),
            item(OMICRON, LINE_OMICRON),
            item(ZETA, "沒有"),
            item("", ""),
        )
    )
    assert [c.symbol for c in out.claims] == ["9101", "9102"]
    assert dict(out.dropped) == {
        DROP_BAD_ITEM: 2,
        DROP_UNRESOLVED: 1,
        DROP_NOT_LISTED: 1,
        DROP_ALREADY_MEMBER: 1,
        DROP_NOT_IN_PAGE: 1,
    }


def test_only_the_first_items_up_to_the_cap_are_read() -> None:
    items = [item(ZETA, LINE_ZETA)] * MAX_ITEMS_PER_PAGE + [item("查無此公司", LINE_ZETA)] * 5
    out = convert(reply(*items))
    assert len(out.claims) == MAX_ITEMS_PER_PAGE
    assert dict(out.dropped) == {}


def test_a_candidate_target_labels_its_claims() -> None:
    target = ThemeTarget(target_id="tc-0123456789ab", kind="candidate", label="Quantum Widget")
    [claim] = convert(reply(item(OMICRON, LINE_OMICRON)), target=target).claims
    assert (claim.theme, claim.kind, claim.label) == (
        "tc-0123456789ab",
        "candidate",
        "Quantum Widget",
    )
