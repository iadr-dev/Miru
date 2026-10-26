"""Characterization of `schemas.alerts.describe` (the fact phrasing of every template) and the
keyword validator's accept/reject messages."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.alerts import (
    AttentionListEntry,
    ClosePrice,
    ConditionModel,
    DailyChange,
    DispositionListEntry,
    EtfPremiumDiscount,
    InstitutionalStreak,
    MarginBalanceChange,
    MaterialAnnouncementPublished,
    MonthRevenuePublished,
    VolumeSurge,
    describe,
    investor_label,
)


@pytest.mark.parametrize(
    ("condition", "phrase"),
    [
        (ClosePrice(comparator="above", value=1000), "收盤價高於 1000"),
        (ClosePrice(comparator="below", value=12.5), "收盤價低於 12.5"),
        (DailyChange(direction="up", percent=5), "單日漲幅達 5%"),
        (DailyChange(direction="down", percent=2.5), "單日跌幅達 2.5%"),
        (VolumeSurge(multiple=2.5), "成交量達近 20 日均量的 2.5 倍"),
        (VolumeSurge(multiple=3, window=10), "成交量達近 10 日均量的 3 倍"),
        (
            InstitutionalStreak(investor="foreign", direction="buy", days=3),
            "外資連續 3 日淨買超",
        ),
        (
            InstitutionalStreak(investor="trust", direction="sell", days=5),
            "投信連續 5 日淨賣超",
        ),
        (
            InstitutionalStreak(investor="dealer", direction="buy", days=2),
            "自營商連續 2 日淨買超",
        ),
        (MarginBalanceChange(side="margin", direction="up", percent=10), "融資餘額單日增加達 10%"),
        (
            MarginBalanceChange(side="short", direction="down", percent=7.5),
            "融券餘額單日減少達 7.5%",
        ),
        (EtfPremiumDiscount(direction="premium", percent=1), "預估溢價幅度達 1%"),
        (EtfPremiumDiscount(direction="discount", percent=0.5), "預估折價幅度達 0.5%"),
        (MaterialAnnouncementPublished(keyword="股利"), "公布含「股利」的重大訊息"),
        (MaterialAnnouncementPublished(), "公布重大訊息"),
        (MaterialAnnouncementPublished(keyword="  "), "公布重大訊息"),
        (AttentionListEntry(), "列入注意股名單"),
        (DispositionListEntry(), "列入處置股名單"),
        (MonthRevenuePublished(), "公布月營收"),
    ],
)
def test_describe_states_the_rule_as_a_fact(condition: ConditionModel, phrase: str) -> None:
    assert describe(condition) == phrase


def test_investor_label() -> None:
    assert [investor_label(i) for i in ("foreign", "trust", "dealer")] == [
        "外資",
        "投信",
        "自營商",
    ]
    with pytest.raises(KeyError):
        investor_label("retail")


def test_keyword_is_whitespace_normalised_and_blank_becomes_none() -> None:
    assert MaterialAnnouncementPublished(keyword="  法說   會 ").keyword == "法說 會"
    assert MaterialAnnouncementPublished(keyword="   ").keyword is None
    assert MaterialAnnouncementPublished(keyword=None).keyword is None


@pytest.mark.parametrize("bad", ["a<b", "x[1]", "a|b", "it's", "a\tb\x00", "a\x07b"])
def test_keyword_rejects_markup_and_control_characters(bad: str) -> None:
    with pytest.raises(ValidationError) as err:
        MaterialAnnouncementPublished(keyword=bad)
    assert "keyword may not contain markup or control characters" in str(err.value)


def test_keyword_rejects_trading_language() -> None:
    with pytest.raises(ValidationError) as err:
        MaterialAnnouncementPublished(keyword="目標價")
    assert "keyword may not contain trading language" in str(err.value)


def test_keyword_rejects_overlong_text() -> None:
    with pytest.raises(ValidationError):
        MaterialAnnouncementPublished(keyword="股" * 21)
