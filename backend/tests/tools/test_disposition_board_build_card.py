"""Characterization of `disposition_board_tools._build_card`: bucket classification, the derived
ratios/flags and the fallbacks, over every branch of the per-symbol assembly."""

from __future__ import annotations

from calendar.trading_days import calendar_from_known_dates
from datetime import date, timedelta

import pytest

from schemas.capabilities import (
    AttentionStockRow,
    DayTradingRow,
    DispositionBoardCardRow,
    DispositionEventRow,
    MarginShortRow,
    SelfAssessedEarningsRow,
)
from schemas.enums import Market
from schemas.source import HealthStatus
from tools.disposition_board_tools import _build_card

_REPORT = date(2026, 9, 4)  # a Friday


def _calendar():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(60)]
    return calendar_from_known_dates(d for d in days if d.weekday() < 5)


def _event(period_end: date, *, security_type: str = "tpex", detail: str | None = None):
    return DispositionEventRow(
        symbol="3008",
        market=Market.TW,
        company_name="大立光",
        announced_date=date(2026, 9, 2),
        reason="r",
        period_start=date(2026, 9, 3),
        period_end=period_end,
        measure="第一次處置",
        detail=detail,
        security_type=security_type,
    )


def _attention(security_type: str = "tpex"):
    return AttentionStockRow(
        symbol="3081",
        market=Market.TW,
        company_name="聯亞",
        flagged_date=_REPORT,
        reason="r",
        security_type=security_type,
    )


def _margin(purchase: int, short: int) -> MarginShortRow:
    return MarginShortRow(
        symbol="3008",
        market=Market.TW,
        trade_date=_REPORT,
        margin_purchase_buy=0,
        margin_purchase_cash_repayment=0,
        margin_purchase_limit=0,
        margin_purchase_sell=0,
        margin_purchase_today_balance=purchase,
        margin_purchase_yesterday_balance=0,
        offset_loan_and_short=0,
        short_sale_buy=0,
        short_sale_cash_repayment=0,
        short_sale_limit=0,
        short_sale_sell=0,
        short_sale_today_balance=short,
        short_sale_yesterday_balance=0,
    )


def _day_trading(volume: int) -> DayTradingRow:
    return DayTradingRow(
        symbol="3008",
        market=Market.TW,
        trade_date=_REPORT,
        buy_after_sale="",
        volume=volume,
        buy_amount=0,
        sell_amount=0,
    )


def _card(**overrides: object) -> DispositionBoardCardRow:
    kwargs: dict[str, object] = {
        "symbol": "3008",
        "market": Market.TW,
        "report_date": _REPORT,
        "calendar": _calendar(),
        "event": None,
        "attention_row": None,
        "flag_names": set(),
        "flag_count": 0,
        "observed_days": 0,
        "close_info": None,
        "prior_close": None,
        "institutional_net": None,
        "margin_row": None,
        "margin_caveat_and_health": ("margin caveat", HealthStatus.OK),
        "day_trading_row": None,
        "self_assessed_row": None,
    }
    kwargs.update(overrides)
    return _build_card(**kwargs)  # type: ignore[arg-type]


def test_an_empty_symbol_is_an_at_risk_card_with_every_lookup_defaulted() -> None:
    card = _card()
    assert card.bucket == "at_risk"
    assert card.company_name == ""
    assert card.security_type == "twse"
    assert card.close is None
    assert card.change_pct is None
    assert card.volume is None
    assert card.turnover is None
    assert card.margin_ratio is None
    assert card.day_trading_ratio is None
    assert card.flags == ()
    assert card.disposition_measure is None
    assert card.disposition_period_start is None
    assert card.disposition_period_end is None
    assert card.matching_interval_minutes is None
    assert card.days_until_exiting is None
    assert card.self_assessed_period is None
    assert card.self_assessed_consolidated is None
    assert card.self_assessed_monthly_pretax_profit is None
    assert card.self_assessed_ytd_pretax_profit is None
    assert card.margin_caveat == "margin caveat"
    assert card.margin_health_status is HealthStatus.OK
    assert card.attention_flags_remaining is None  # 3 remaining is not "close enough"
    assert card.estimated_days_until_disposition is None  # no flags -> no rate


def test_attention_row_supplies_the_name_and_type_when_there_is_no_event() -> None:
    card = _card(attention_row=_attention("tpex"), symbol="3081")
    assert (card.company_name, card.security_type) == ("聯亞", "tpex")
    assert card.bucket == "at_risk"


def test_event_wins_over_the_attention_row_for_name_and_type() -> None:
    card = _card(
        event=_event(date(2026, 9, 30), security_type="twse"),
        attention_row=_attention("tpex"),
    )
    assert (card.company_name, card.security_type) == ("大立光", "twse")


def test_at_risk_projection_uses_the_flag_count_and_observed_rate() -> None:
    card = _card(flag_count=2, observed_days=4, flag_names={"注"})
    assert card.bucket == "at_risk"
    assert card.attention_flags_remaining == 1
    assert card.estimated_days_until_disposition == 2  # rate 2/4 -> 3 flags in 6 days; 6 - 4 = 2
    assert card.days_until_exiting is None
    assert card.flags == ("注",)


def test_a_symbol_in_disposition_has_no_projection_and_counts_days_after_today() -> None:
    card = _card(event=_event(date(2026, 9, 30)))
    assert card.bucket == "in_disposition"
    assert card.attention_flags_remaining is None
    assert card.estimated_days_until_disposition is None
    # Sep 4..30 inclusive has 19 trading days; today itself is not "remaining".
    assert card.days_until_exiting == 18
    assert card.disposition_measure == "第一次處置"
    assert card.disposition_period_start == date(2026, 9, 3)
    assert card.disposition_period_end == date(2026, 9, 30)
    assert card.flags == ("處",)


def test_exiting_soon_at_the_window_edge_and_just_outside_it() -> None:
    inside = _card(event=_event(date(2026, 9, 10)))  # Sep 4, 7, 8, 9, 10 -> 5 days
    assert inside.bucket == "exiting_soon"
    assert inside.days_until_exiting == 4
    outside = _card(event=_event(date(2026, 9, 11)))  # 6 days
    assert outside.bucket == "in_disposition"
    assert outside.days_until_exiting == 5


def test_a_disposition_ending_today_is_exiting_soon_zero_days_out() -> None:
    card = _card(event=_event(_REPORT))
    assert card.bucket == "exiting_soon"
    assert card.days_until_exiting == 0


def test_a_disposition_that_already_ended_is_in_disposition_zero_days_out() -> None:
    card = _card(event=_event(date(2026, 9, 3)))
    assert card.bucket == "in_disposition"
    assert card.days_until_exiting == 0


def test_price_fields_and_change_percentage() -> None:
    card = _card(close_info=(110.0, 2000, 220000.0), prior_close=100.0)
    assert (card.close, card.volume, card.turnover) == (110.0, 2000, 220000.0)
    assert card.change_pct == pytest.approx(10.0)
    assert _card(close_info=(110.0, 2000, None), prior_close=100.0).turnover is None
    assert _card(close_info=(110.0, 2000, None), prior_close=0.0).change_pct is None
    assert _card(close_info=(110.0, 2000, None), prior_close=None).change_pct is None
    assert _card(close_info=None, prior_close=100.0).change_pct is None


def test_margin_flags_and_ratio() -> None:
    both = _card(margin_row=_margin(1000, 250))
    assert both.flags == ("券", "資")
    assert both.margin_ratio == pytest.approx(25.0)
    only_purchase = _card(margin_row=_margin(1000, 0))
    assert only_purchase.flags == ("資",)
    assert only_purchase.margin_ratio == pytest.approx(0.0)
    only_short = _card(margin_row=_margin(0, 50))
    assert only_short.flags == ("券",)
    assert only_short.margin_ratio is None  # no margin balance to divide by
    empty = _card(margin_row=_margin(0, 0))
    assert empty.flags == ()
    assert empty.margin_ratio is None


def test_day_trading_flag_and_ratio_need_volume_on_both_sides() -> None:
    card = _card(close_info=(10.0, 1000, None), day_trading_row=_day_trading(250))
    assert card.flags == ("沖",)
    assert card.day_trading_ratio == pytest.approx(25.0)
    no_price_volume = _card(close_info=None, day_trading_row=_day_trading(250))
    assert no_price_volume.flags == ("沖",)
    assert no_price_volume.day_trading_ratio is None
    zero_volume = _card(close_info=(10.0, 1000, None), day_trading_row=_day_trading(0))
    assert zero_volume.flags == ()
    assert zero_volume.day_trading_ratio == pytest.approx(0.0)
    zero_bar_volume = _card(close_info=(10.0, 0, None), day_trading_row=_day_trading(5))
    assert zero_bar_volume.day_trading_ratio is None


def test_flags_are_the_sorted_union_of_names_and_derived_flags() -> None:
    card = _card(
        event=_event(date(2026, 9, 30)),
        flag_names={"注", "x"},
        close_info=(10.0, 1000, None),
        margin_row=_margin(10, 10),
        day_trading_row=_day_trading(1),
    )
    assert card.flags == tuple(sorted({"注", "x", "處", "資", "券", "沖"}))


def test_matching_interval_is_parsed_from_the_event_detail() -> None:
    card = _card(event=_event(date(2026, 9, 30), detail="每5分鐘撮合一次"))
    assert card.matching_interval_minutes == 5
    assert _card(event=_event(date(2026, 9, 30))).matching_interval_minutes is None


def test_self_assessed_earnings_pass_through() -> None:
    row = SelfAssessedEarningsRow(
        symbol="3008",
        market=Market.TW,
        company_name="大立光",
        revenue_period="2026-08",
        consolidated=True,
        monthly_pretax_profit=123,
        ytd_pretax_profit=456,
    )
    card = _card(self_assessed_row=row)
    assert card.self_assessed_period == "2026-08"
    assert card.self_assessed_consolidated is True
    assert card.self_assessed_monthly_pretax_profit == 123
    assert card.self_assessed_ytd_pretax_profit == 456
    assert card.institutional_net is None
    assert _card(institutional_net=-7).institutional_net == -7
