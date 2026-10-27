"""Event extraction from stored rows, one source dataset per event type."""

from __future__ import annotations

from datetime import date, timedelta

from analytics.event_study import (
    EventDateFilter,
    EventType,
    attention_events,
    buyback_events,
    capital_reduction_events,
    disposition_events,
)
from schemas.capabilities import (
    AttentionStockRow,
    CapitalReductionReferencePriceRow,
    DispositionEventRow,
    MaterialAnnouncementRow,
)
from schemas.enums import Market

_NO_FILTER = EventDateFilter()


def _calendar(start: date, days: int) -> list[date]:
    out: list[date] = []
    cursor = start
    while len(out) < days:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _disposition(
    symbol: str, start: date, end: date, *, announced: date | None = None, name: str = "測試"
) -> DispositionEventRow:
    return DispositionEventRow(
        symbol=symbol,
        market=Market.TW,
        company_name=name,
        announced_date=announced or start - timedelta(days=1),
        reason="連續三次",
        period_start=start,
        period_end=end,
        measure="第一次處置",
        security_type="twse",
    )


def test_disposition_start_uses_the_period_start_and_collapses_repeated_snapshots() -> None:
    monday = date(2026, 9, 7)
    older = _disposition("2330", monday, monday + timedelta(days=4), name="舊名")
    newer = _disposition("2330", monday, monday + timedelta(days=7), name="新名")  # end moved

    result = disposition_events(
        [older, newer, _disposition("1101", monday + timedelta(days=7), monday + timedelta(10))],
        EventType.DISPOSITION_START,
        _NO_FILTER,
        _calendar(monday, 30),
    )

    assert [(e.symbol, e.event_date) for e in result.events] == [
        ("2330", monday),
        ("1101", monday + timedelta(days=7)),
    ]
    assert result.events[0].name == "新名"  # the later snapshot wins
    assert result.rows_seen == 3


def test_warrants_are_left_out_and_counted() -> None:
    monday = date(2026, 9, 7)
    rows = [
        _disposition("2330", monday, monday + timedelta(days=4)),
        _disposition("086845", monday, monday + timedelta(days=4)),  # a warrant code
    ]

    result = disposition_events(rows, EventType.DISPOSITION_START, _NO_FILTER, [])

    assert [e.symbol for e in result.events] == ["2330"]
    assert result.excluded_non_equity == 1


def test_disposition_end_is_the_first_day_after_the_period() -> None:
    friday = date(2026, 9, 11)
    row = _disposition("2330", date(2026, 9, 7), friday)

    result = disposition_events(
        [row], EventType.DISPOSITION_END, _NO_FILTER, _calendar(date(2026, 9, 1), 30)
    )

    assert [(e.symbol, e.event_date) for e in result.events] == [("2330", date(2026, 9, 12))]


def test_a_period_followed_straight_away_by_another_is_an_extension_not_a_release() -> None:
    calendar = _calendar(date(2026, 9, 1), 40)
    first = _disposition("2330", date(2026, 9, 7), date(2026, 9, 11))
    # the next period starts on the next trading day (Monday 9/14): the stock never got released
    second = _disposition("2330", date(2026, 9, 14), date(2026, 9, 25))
    other = _disposition("1101", date(2026, 9, 7), date(2026, 9, 11))

    result = disposition_events(
        [first, second, other], EventType.DISPOSITION_END, _NO_FILTER, calendar
    )

    released = {(e.symbol, e.event_date) for e in result.events}
    assert released == {("2330", date(2026, 9, 26)), ("1101", date(2026, 9, 12))}
    assert result.excluded_extended == 1


def test_the_symbol_and_date_filters_apply_to_the_event_date() -> None:
    rows = [
        _disposition("2330", date(2026, 9, 7), date(2026, 9, 11)),
        _disposition("2330", date(2026, 10, 5), date(2026, 10, 9)),
        _disposition("1101", date(2026, 10, 5), date(2026, 10, 9)),
    ]

    result = disposition_events(
        rows,
        EventType.DISPOSITION_START,
        EventDateFilter(symbol="2330", start=date(2026, 10, 1)),
        [],
    )

    assert [(e.symbol, e.event_date) for e in result.events] == [("2330", date(2026, 10, 5))]
    assert result.excluded_outside_filter == 2


def test_attention_listing_uses_the_flagged_date_and_dedupes_per_day() -> None:
    def row(symbol: str, flagged: date) -> AttentionStockRow:
        return AttentionStockRow(
            symbol=symbol,
            market=Market.TW,
            company_name="測試",
            flagged_date=flagged,
            reason="累積漲幅",
            security_type="tpex",
        )

    # the morning fetch repeats yesterday's notice in the next snapshot
    rows = [
        row("3441", date(2026, 10, 1)),
        row("3441", date(2026, 10, 1)),
        row("3441", date(2026, 10, 2)),
    ]

    result = attention_events(rows, _NO_FILTER)

    assert [(e.symbol, e.event_date) for e in result.events] == [
        ("3441", date(2026, 10, 1)),
        ("3441", date(2026, 10, 2)),
    ]


def test_capital_reduction_resume_uses_the_row_trade_date() -> None:
    def row(symbol: str, day: date) -> CapitalReductionReferencePriceRow:
        return CapitalReductionReferencePriceRow(
            symbol=symbol,
            market=Market.TW,
            trade_date=day,
            closing_price_on_last_trading_day=10.0,
            post_reduction_reference_price=20.0,
            limit_up=22.0,
            limit_down=18.0,
            opening_reference_price=20.0,
            exright_reference_price=20.0,
            reason_for_capital_reduction="彌補虧損",
        )

    result = capital_reduction_events(
        [
            row("1234", date(2024, 5, 20)),
            row("1234", date(2024, 5, 20)),
            row("1234", date(2025, 2, 3)),
        ],
        EventDateFilter(symbol="1234"),
    )

    assert [e.event_date for e in result.events] == [date(2024, 5, 20), date(2025, 2, 3)]
    assert all(e.name is None for e in result.events)


def _announcement(symbol: str, subject: str, detail: str, day: date) -> MaterialAnnouncementRow:
    return MaterialAnnouncementRow(
        symbol=symbol,
        market=Market.TW,
        company_name="測試",
        announced_date=day,
        announced_time="16:00:00",
        subject=subject,
        detail=detail,
        qualifying_clause="第35款",
        category="庫藏股/減資",
    )


_BOARD_DETAIL = (
    "1.董事會決議日期:115/09/03\n"
    "4.買回股份總金額上限(元):670,456,301\n"
    "5.預定買回之期間:115/09/04~115/11/02\n"
    "6.預定買回之數量(股):400,000\n"
)
_DONE_DETAIL = (
    "1.原預定買回股份總金額上限(元):9,396,490,656\n"
    "2.原預定買回之期間:115/08/07~115/10/05\n"
    "5.本次實際買回期間:115/08/10~115/10/01\n"
    "6.本次已買回股份數量(股):16,000,000\n"
)


def test_buyback_start_reads_the_planned_period_of_a_board_resolution() -> None:
    day = date(2026, 9, 3)
    rows = [
        _announcement("6655", "公告本公司董事會決議買回庫藏股", _BOARD_DETAIL, day),
        # the correction of the same filing collapses into it
        _announcement(
            "6655",
            "公告本公司董事會決議買回庫藏股 (更正買回股份總金額上限、預計買回期間)",
            _BOARD_DETAIL,
            day + timedelta(days=5),
        ),
        _announcement("2536", "公告本公司買回庫藏股已執行完畢", _DONE_DETAIL, day),
        _announcement(
            "7818", "公告本公司董事會決議註銷庫藏股暨訂定減資基準日", "減資基準日:115/09/10", day
        ),
        _announcement("6908", "代子公司Winking Studios Limited公告庫藏股執行情形", "x", day),
    ]

    result = buyback_events(rows, EventType.BUYBACK_START, _NO_FILTER)

    assert [(e.symbol, e.event_date) for e in result.events] == [("6655", date(2026, 9, 4))]


def test_buyback_end_reads_the_actual_period_of_a_completion_filing() -> None:
    day = date(2026, 10, 1)
    rows = [
        _announcement("2536", "公告本公司買回庫藏股已執行完畢", _DONE_DETAIL, day),
        _announcement("6655", "公告本公司董事會決議買回庫藏股", _BOARD_DETAIL, day),
        _announcement(
            "2023", "公告本公司第二十五次買回庫藏股執行情形", _DONE_DETAIL, day
        ),  # interim
        _announcement("5292", "公告本公司買回庫藏股期間屆滿執行情形", "no dates here", day),
    ]

    result = buyback_events(rows, EventType.BUYBACK_END, _NO_FILTER)

    assert [(e.symbol, e.event_date) for e in result.events] == [("2536", date(2026, 10, 1))]
    assert result.excluded_unparsed == 1


def test_roc_ranges_with_full_width_separators_parse() -> None:
    tilde = chr(0xFF5E)
    detail = f"5.預定買回之期間：115/09/04{tilde}115/11/02"
    rows = [_announcement("6655", "公告本公司董事會決議買回庫藏股", detail, date(2026, 9, 3))]

    result = buyback_events(rows, EventType.BUYBACK_START, _NO_FILTER)

    assert [e.event_date for e in result.events] == [date(2026, 9, 4)]
