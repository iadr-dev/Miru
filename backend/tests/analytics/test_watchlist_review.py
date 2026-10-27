"""`analytics.watchlist_review`: the week's move of each symbol, the event window, the freshness
verdict and the rendered report. All pure: bars and events are handed in, no store, no clock."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from analytics.watchlist_review import (
    NOT_COVERED_TW,
    NOT_COVERED_US,
    Freshness,
    MarketReview,
    ReviewEvent,
    ReviewStatus,
    SourceIssue,
    WeeklyMove,
    WeeklyReview,
    event_window,
    freshness_of,
    has_basis_break,
    render_notice,
    render_report,
    select_events,
    select_extremes,
    statutory_deadlines,
    week_bounds,
    weekly_move,
)
from governance.textscan import check_output
from schemas.enums import Market

FRI = date(2026, 10, 23)
PREV_FRI = date(2026, 10, 16)
SAT = date(2026, 10, 24)


def _weekdays(start: date, end: date) -> list[date]:
    return [
        start + timedelta(days=n)
        for n in range((end - start).days + 1)
        if (start + timedelta(days=n)).weekday() < 5
    ]


# ---- week bounds ------------------------------------------------------------------------------


def test_weekend_run_compares_friday_with_the_previous_friday() -> None:
    dates = _weekdays(date(2026, 10, 5), FRI)
    assert week_bounds(dates, SAT) == (PREV_FRI, FRI)


def test_weekday_run_uses_the_latest_stored_day_not_today() -> None:
    dates = _weekdays(date(2026, 10, 5), date(2026, 10, 21))  # Wed is the newest stored day
    assert week_bounds(dates, date(2026, 10, 22)) == (date(2026, 10, 14), date(2026, 10, 21))


def test_a_holiday_friday_moves_both_ends_to_the_nearest_trading_day() -> None:
    dates = [d for d in _weekdays(date(2026, 10, 5), FRI) if d != FRI]  # Friday closed
    base, end = week_bounds(dates, SAT)
    assert end == date(2026, 10, 22)
    assert base == date(2026, 10, 15)


def test_a_holiday_on_the_base_friday_falls_back_to_the_day_before() -> None:
    dates = [d for d in _weekdays(date(2026, 10, 5), FRI) if d != PREV_FRI]
    assert week_bounds(dates, SAT) == (date(2026, 10, 15), FRI)


def test_a_long_closure_uses_the_last_day_before_it() -> None:
    reopened = date(2027, 2, 12)
    before = _weekdays(date(2027, 1, 25), date(2027, 2, 5))
    dates = [*before, reopened]
    assert week_bounds(dates, date(2027, 2, 13)) == (date(2027, 2, 5), reopened)


def test_short_history_has_no_base_and_no_history_has_neither() -> None:
    assert week_bounds([date(2026, 10, 21), FRI], SAT) == (None, FRI)
    assert week_bounds([], SAT) == (None, None)
    assert week_bounds([date(2026, 11, 2)], SAT) == (None, None)  # nothing on or before as_of


# ---- the move of one symbol -------------------------------------------------------------------


def _move(bars: list[tuple[date, float]], market: Market = Market.TW) -> WeeklyMove:
    return weekly_move("2330", market, bars, base_date=PREV_FRI, end_date=FRI)


def test_move_is_the_close_to_close_change_in_percent() -> None:
    move = _move([(PREV_FRI, 1000.0), (date(2026, 10, 20), 1010.0), (FRI, 1032.0)])
    assert move.status == "ok"
    assert move.change_pct == pytest.approx(3.2)
    assert (move.base_close, move.end_close) == (1000.0, 1032.0)
    assert (move.base_date, move.end_date) == (PREV_FRI, FRI)
    assert not move.basis_break


def test_move_can_be_negative_and_is_rounded_to_two_places() -> None:
    move = _move([(PREV_FRI, 300.0), (FRI, 299.0)])
    assert move.change_pct == -0.33


def test_no_close_on_the_latest_day_is_a_status_not_a_zero() -> None:
    move = _move([(PREV_FRI, 100.0), (date(2026, 10, 22), 101.0)])  # suspended on Friday
    assert move.status == "no_end_price"
    assert move.change_pct is None


def test_a_new_listing_has_no_previous_week_close() -> None:
    move = _move([(date(2026, 10, 20), 50.0), (FRI, 55.0)])
    assert move.status == "no_base_price"
    assert move.end_close == 55.0
    assert move.change_pct is None


def test_a_symbol_without_any_bar_has_no_data() -> None:
    assert _move([]).status == "no_data"
    assert weekly_move("X", Market.TW, [(FRI, 1.0)], base_date=None, end_date=None).status == (
        "no_data"
    )


def test_a_zero_close_is_not_used_as_a_base() -> None:
    assert _move([(PREV_FRI, 0.0), (FRI, 10.0)]).status == "no_base_price"


def test_a_split_like_break_is_flagged_and_left_unadjusted() -> None:
    # 4-for-1 split in the window: the raw close drops 75%.
    move = _move([(PREV_FRI, 400.0), (date(2026, 10, 20), 100.0), (FRI, 102.0)])
    assert move.basis_break is True
    assert move.change_pct == -74.5  # the raw, unadjusted figure


def test_break_thresholds_differ_by_market() -> None:
    # -30% in a day is impossible in Taiwan (10% limit) but a real move in the US.
    bars = [(PREV_FRI, 100.0), (FRI, 70.0)]
    assert has_basis_break(bars, Market.TW) is True
    assert has_basis_break(bars, Market.US) is False
    assert has_basis_break([(PREV_FRI, 100.0), (FRI, 40.0)], Market.US) is True
    assert has_basis_break([(PREV_FRI, 100.0), (FRI, 112.0)], Market.TW) is True
    assert has_basis_break([(PREV_FRI, 100.0), (FRI, 112.0)], Market.US) is False
    assert has_basis_break([(PREV_FRI, 100.0)], Market.TW) is False


# ---- best and worst ---------------------------------------------------------------------------


def _ok(symbol: str, pct: float, *, brk: bool = False) -> WeeklyMove:
    return WeeklyMove(
        symbol=symbol,
        market=Market.TW,
        status="ok",
        end_date=FRI,
        base_date=PREV_FRI,
        end_close=100 + pct,
        base_close=100.0,
        change_pct=pct,
        basis_break=brk,
    )


def test_extremes_are_the_top_and_bottom_n_without_overlap() -> None:
    moves = [_ok("A", 5.0), _ok("B", 2.0), _ok("C", -1.0), _ok("D", -4.0), _ok("E", 0.5)]
    best, worst = select_extremes(moves, 2)
    assert [m.symbol for m in best] == ["A", "B"]
    assert [m.symbol for m in worst] == ["D", "C"]
    assert not {m.symbol for m in best} & {m.symbol for m in worst}


def test_a_flat_or_all_down_list_never_calls_a_decliner_a_gainer() -> None:
    best, worst = select_extremes([_ok("A", -1.0), _ok("B", -2.0), _ok("C", 0.0)], 3)
    assert best == ()
    assert [m.symbol for m in worst] == ["B", "A"]


def test_extremes_skip_broken_basis_and_uncomputed_symbols() -> None:
    gap = WeeklyMove(symbol="G", market=Market.TW, status="no_base_price")
    best, worst = select_extremes([_ok("A", 90.0, brk=True), _ok("B", 1.0), gap], 3)
    assert [m.symbol for m in best] == ["B"]
    assert worst == ()


def test_ties_are_ordered_by_symbol_so_the_report_is_stable() -> None:
    best, _ = select_extremes([_ok("B", 2.0), _ok("A", 2.0)], 2)
    assert [m.symbol for m in best] == ["B", "A"]  # reversed ascending order is deterministic


# ---- the event window -------------------------------------------------------------------------


def _event(symbol: str | None, day: date, kind: str = "ex_dividend") -> ReviewEvent:
    return ReviewEvent.model_validate(
        {"symbol": symbol, "market": Market.TW, "kind": kind, "event_date": day, "source": "s"}
    )


def test_window_is_the_run_day_and_the_next_six_days_inclusive() -> None:
    assert event_window(SAT) == (SAT, date(2026, 10, 30))
    assert event_window(SAT, 1) == (SAT, SAT)


def test_both_ends_are_inclusive_and_neighbours_are_out() -> None:
    start, end = event_window(SAT)
    events = [
        _event("A", start - timedelta(days=1)),
        _event("B", start),
        _event("C", end),
        _event("D", end + timedelta(days=1)),
    ]
    assert [e.symbol for e in select_events(events, start, end)] == ["B", "C"]


def test_weekend_dates_are_events_like_any_other() -> None:
    start, end = event_window(date(2026, 10, 26))  # a Monday run: Mon..Sun
    sunday = date(2026, 11, 1)
    assert sunday.weekday() == 6
    assert [e.event_date for e in select_events([_event("A", sunday)], start, end)] == [sunday]


def test_events_are_sorted_and_deduplicated() -> None:
    start, end = event_window(SAT)
    day = date(2026, 10, 27)
    events = [_event("B", day), _event("A", day), _event("A", day), _event("C", date(2026, 10, 26))]
    chosen = select_events(events, start, end)
    assert [(e.event_date, e.symbol) for e in chosen] == [
        (date(2026, 10, 26), "C"),
        (day, "A"),
        (day, "B"),
    ]


def test_market_wide_events_sort_without_a_symbol() -> None:
    start, end = event_window(SAT)
    chosen = select_events(
        [_event(None, date(2026, 10, 27)), _event("A", date(2026, 10, 27))], start, end
    )
    assert [e.symbol for e in chosen] == [None, "A"]


# ---- statutory deadlines ----------------------------------------------------------------------


def test_the_third_quarter_report_deadline_is_listed_in_its_week() -> None:
    found = statutory_deadlines(date(2026, 11, 12), date(2026, 11, 18))
    assert [(e.kind, e.event_date, e.detail) for e in found] == [
        ("financial_deadline", date(2026, 11, 14), "第3季財報（上市櫃公司）")
    ]
    assert all(e.symbol is None for e in found)


def test_the_annual_report_deadline_and_the_monthly_revenue_deadline() -> None:
    annual = statutory_deadlines(date(2027, 3, 28), date(2027, 4, 3))
    assert [(e.detail, e.event_date) for e in annual] == [("年報（上市櫃公司）", date(2027, 3, 31))]
    revenue = statutory_deadlines(date(2026, 11, 8), date(2026, 11, 14))
    assert ("10 月營收", date(2026, 11, 10)) in [(e.detail, e.event_date) for e in revenue]


def test_no_deadline_in_an_ordinary_week() -> None:
    assert statutory_deadlines(date(2026, 10, 24), date(2026, 10, 30)) == []


def test_deadlines_cross_a_year_boundary() -> None:
    found = statutory_deadlines(date(2026, 12, 29), date(2027, 1, 12))
    assert [(e.detail, e.event_date) for e in found] == [("12 月營收", date(2027, 1, 10))]


# ---- freshness --------------------------------------------------------------------------------


def test_data_older_than_the_expected_day_is_stale() -> None:
    assert freshness_of(Market.TW, date(2026, 10, 22), FRI).stale is True
    assert freshness_of(Market.TW, FRI, FRI).stale is False
    assert freshness_of(Market.TW, None, FRI).stale is True
    assert freshness_of(Market.TW, FRI, None).stale is False


# ---- rendering --------------------------------------------------------------------------------


def _section(  # noqa: PLR0913 — one keyword per part of a market section
    market: Market = Market.TW,
    *,
    moves: tuple[WeeklyMove, ...] = (),
    events: tuple[ReviewEvent, ...] = (),
    statuses: tuple[ReviewStatus, ...] = (),
    fresh: Freshness | None = None,
    issues: tuple[SourceIssue, ...] = (),
    total: int | None = None,
    covered: int | None = None,
) -> MarketReview:
    return MarketReview(
        market=market,
        symbols_total=total if total is not None else len(moves),
        symbols_covered=covered if covered is not None else len(moves),
        moves=moves,
        events=events,
        statuses=statuses,
        freshness=fresh
        or Freshness(market=market, data_as_of=FRI, base_date=PREV_FRI, expected_as_of=FRI),
        issues=issues,
    )


def _review(
    *sections: MarketReview, total: int | None = None, covered: int | None = None
) -> WeeklyReview:
    start, end = event_window(SAT)
    return WeeklyReview(
        run_date=SAT,
        window_start=start,
        window_end=end,
        groups=2,
        symbols_total=total if total is not None else sum(s.symbols_total for s in sections),
        symbols_covered=covered
        if covered is not None
        else sum(s.symbols_covered for s in sections),
        markets=sections,
    )


def _clean(text: str) -> None:
    verdict = check_output(text, has_market_data=False)
    assert verdict.reason is None, verdict.redacted_text


def test_empty_watchlist_is_one_calm_line() -> None:
    text = render_report(_review(total=0, covered=0))
    assert text.splitlines()[0] == "本週沒有可回顧的自選股：你的自選股群組目前是空的。"
    assert "###" not in text
    _clean(text)
    assert render_notice(_review(total=0, covered=0)).startswith("本週沒有可回顧的自選股")


def test_a_taiwan_section_shows_moves_events_status_and_freshness() -> None:
    named = _ok("2330", 3.2).model_copy(
        update={"name": "台積電", "base_close": 1000.0, "end_close": 1032.0}
    )
    section = _section(
        moves=(
            named,
            _ok("2317", -4.0),
            WeeklyMove(symbol="6999", market=Market.TW, status="no_base_price", end_date=FRI),
        ),
        events=(
            ReviewEvent(
                symbol="2330",
                market=Market.TW,
                name="台積電",
                kind="ex_dividend",
                event_date=date(2026, 10, 28),
                source="股利公告資料",
                detail="現金股利每股 5",
            ),
        ),
        statuses=(
            ReviewStatus(
                symbol="2317",
                market=Market.TW,
                kind="attention_listed",
                since=FRI,
                source="交易所注意股公告",
            ),
        ),
    )
    text = render_report(_review(section))
    assert "### 台股（3 檔）" in text
    assert "漲幅居前：2330 台積電 +3.20%（1,000.00 → 1,032.00）" in text
    assert "跌幅居前：2317 -4.00%" in text
    assert "無法計算：1 檔" in text
    assert "6999（前一週沒有收盤價（可能是新掛牌））" in text
    assert "價格未還原權值" in text
    assert "2026-10-28 2330 台積電 除息，現金股利每股 5（來源：股利公告資料）" in text
    assert "2317 近期列入注意股" in text
    assert "資料日期：收盤資料截至 2026-10-23。" in text
    assert "未來 7 天事件**（2026-10-24 至 2026-10-30）" in text
    assert NOT_COVERED_TW in text
    assert "完整內容請見此對話。" in text
    _clean(text)


def test_no_events_says_so_instead_of_omitting_the_section() -> None:
    text = render_report(_review(_section(moves=(_ok("2330", 1.0),))))
    assert "查無已公布的事件。" in text


def test_stale_data_and_unreachable_sources_are_stated_plainly() -> None:
    fresh = Freshness(
        market=Market.TW,
        data_as_of=date(2026, 10, 21),
        base_date=date(2026, 10, 14),
        expected_as_of=FRI,
        stale=True,
    )
    section = _section(
        moves=(_ok("2330", 1.0),),
        fresh=fresh,
        issues=(SourceIssue(source="交易所行事曆", market=Market.TW, detail="部分查詢逾時或失敗"),),
    )
    text = render_report(_review(section))
    assert "收盤資料截至 2026-10-21（較預期的 2026-10-23 舊，資料可能尚未更新）" in text
    assert "注意：交易所行事曆暫時無法讀取（部分查詢逾時或失敗），相關事件可能不完整。" in text
    assert "資料較舊" in render_notice(_review(section))
    _clean(text)


def test_a_market_without_any_price_data_says_so() -> None:
    fresh = Freshness(market=Market.US, data_as_of=None, stale=True)
    gaps = (WeeklyMove(symbol="AAPL", market=Market.US, status="no_data"),)
    text = render_report(_review(_section(Market.US, moves=gaps, fresh=fresh)))
    assert "目前無法計算（缺少足夠的收盤資料）" in text
    assert "資料日期：目前沒有可用的收盤資料。" in text
    assert "AAPL（查無收盤資料）" in text


def test_mixed_markets_are_labelled_separately_with_their_own_coverage_notes() -> None:
    us = _section(Market.US, moves=(_ok("AAPL", 2.0).model_copy(update={"market": Market.US}),))
    text = render_report(_review(_section(moves=(_ok("2330", 1.0),)), us))
    assert text.index("### 台股") < text.index("### 美股")
    assert NOT_COVERED_TW in text
    assert NOT_COVERED_US in text
    _clean(text)


def test_us_only_review_does_not_mention_taiwan_gaps() -> None:
    us = _section(Market.US, moves=(_ok("AAPL", 2.0).model_copy(update={"market": Market.US}),))
    text = render_report(_review(us))
    assert NOT_COVERED_US in text
    assert NOT_COVERED_TW not in text


def test_over_the_cap_the_true_count_is_reported() -> None:
    section = _section(
        moves=tuple(_ok(f"{1000 + n}", 1.0) for n in range(60)), total=75, covered=60
    )
    text = render_report(_review(section, total=75, covered=60))
    assert "共 75 檔；本次涵蓋 60 檔，其餘 15 檔超過單次上限而未納入" in text
    assert "### 台股（75 檔，本次涵蓋 60 檔）" in text


def test_a_basis_break_symbol_is_disclosed_and_not_ranked() -> None:
    broken = _ok("0050", -74.0, brk=True)
    text = render_report(_review(_section(moves=(broken, _ok("2330", 1.0)))))
    assert "價格基準可能受分割、減資或面額變更影響" in text
    assert "跌幅居前" not in text
    assert "漲幅居前：2330" in text


def test_the_notice_is_a_single_line_with_the_highlights() -> None:
    notice = render_notice(_review(_section(moves=(_ok("2330", 3.0), _ok("2317", -2.0)))))
    assert "\n" not in notice
    assert "共 2 檔" in notice
    assert "台股未來 7 天事件 0 項" in notice
    assert "週漲幅居前 2330 +3.00%" in notice
    assert "跌幅居前 2317 -2.00%" in notice
    _clean(notice)


def test_nothing_in_the_report_reads_as_advice() -> None:
    text = render_report(_review(_section(moves=(_ok("2330", 9.0), _ok("2317", -9.0)))))
    for word in ("買進", "賣出", "目標價", "推薦", "加碼", "減碼"):
        assert word not in text
