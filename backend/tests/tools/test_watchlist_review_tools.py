"""`get_watchlist_weekly_review`: the user's own watchlists reviewed from the stored data. A real
`ParquetStore` in a temp dir holds the closes, dividend rows and risk lists; the reader is a fake
that records the user id it was asked for; the exchange calendar feed is stubbed."""

from __future__ import annotations

from calendar.models import CalendarEvent, EventState, EventType
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from ingest.jobs import (
    ATTENTION_STOCKS_DATASET,
    DISPOSITION_EVENTS_DATASET,
    DIVIDENDS_JOB,
    PRICE_EOD_JOB,
    STOCK_INFO_JOB,
)
from ingest.store import ParquetStore
from ingest.us_jobs import US_PRICE_EOD_DATASET
from schemas.enums import DataClass, Market, Tier, UserMarket
from schemas.source import HealthStatus, SourceHealth
from tools import watchlist_review_tools as module
from tools.account_tools import (
    NotificationSummary,
    UsageSnapshot,
    WatchlistEntry,
    WatchlistGroupSummary,
    WatchlistsSnapshot,
)
from tools.base import SignInRequired, ToolContext, dispatch_tool
from tools.watchlist_review_tools import (
    MAX_REVIEW_SYMBOLS,
    GetWatchlistWeeklyReviewInput,
    GetWatchlistWeeklyReviewOutput,
    GetWatchlistWeeklyReviewTool,
    expected_trading_day,
    feed_events_to_review,
)

ALICE = ToolContext(user_id="alice", tier=Tier.FREE)
# Saturday 09:00 in Taipei.
NOW = datetime(2026, 10, 24, 1, 0, tzinfo=UTC)
FRIDAY = date(2026, 10, 23)
PREV_FRIDAY = date(2026, 10, 16)


class FakeReader:
    def __init__(self, snapshot: WatchlistsSnapshot) -> None:
        self._snapshot = snapshot
        self.users: list[str] = []
        self.calls: list[dict[str, object]] = []

    async def watchlists(
        self, *, user_id: str, market: UserMarket | None, max_items: int
    ) -> WatchlistsSnapshot:
        self.users.append(user_id)
        self.calls.append({"market": market, "max_items": max_items})
        return self._snapshot

    async def automations(self, **_: object) -> object:  # pragma: no cover - unused
        raise NotImplementedError

    async def usage(self, **_: object) -> UsageSnapshot:  # pragma: no cover - unused
        raise NotImplementedError

    async def notifications(self, **_: object) -> list[NotificationSummary]:  # pragma: no cover
        raise NotImplementedError


def _group(name: str, market: Market, symbols: list[str]) -> WatchlistGroupSummary:
    assert market in (Market.TW, Market.US)
    return WatchlistGroupSummary(
        name=name,
        market=market,  # type: ignore[arg-type]
        asset_class="stock",
        is_default=False,
        item_count=len(symbols),
        items=tuple(WatchlistEntry(symbol=s, market=market) for s in symbols),  # type: ignore[arg-type]
    )


def _snapshot(*groups: WatchlistGroupSummary) -> WatchlistsSnapshot:
    return WatchlistsSnapshot(groups=groups, total_items=sum(g.item_count for g in groups))


def _bar(symbol: str, market: Market, day: date, close: float) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": market.value,
        "trade_date": day.isoformat(),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "turnover": close * 1000,
    }


def _weekdays(start: date, end: date) -> list[date]:
    days = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    return [d for d in days if d.weekday() < 5]


def _seed_prices(
    store: ParquetStore,
    market: Market,
    closes: dict[str, dict[date, float]],
    days: list[date],
) -> None:
    dataset = PRICE_EOD_JOB.dataset if market is Market.TW else US_PRICE_EOD_DATASET
    for day in days:
        rows = [_bar(s, market, day, c[day]) for s, c in closes.items() if day in c]
        store.write_partition(dataset, market.value, day, rows)


def _seed_week(store: ParquetStore, last: date = FRIDAY) -> None:
    days = _weekdays(date(2026, 10, 12), last)
    base = dict.fromkeys(days, 100.0)
    tw = {
        "2330": {d: 1030.0 if d == last else 1000.0 for d in days},
        "2317": {d: 200.0 if d < last else 190.0 for d in days},
        "6999": {d: 50.0 for d in days if d >= date(2026, 10, 20)},  # listed this week
    }
    store.write_partition(
        STOCK_INFO_JOB.dataset,
        "TW",
        last,
        [
            {
                "symbol": "2330",
                "market": "TW",
                "name": "台積電",
                "industry_category": "半導體",
                "security_type": "twse",
                "as_of": last.isoformat(),
            }
        ],
    )
    _seed_prices(store, Market.TW, tw, days)
    _seed_prices(
        store,
        Market.US,
        {"AAPL": {d: 200.0 + (5.0 if d == last else 0.0) for d in days}, "MSFT": base},
        days,
    )


def _seed_events(store: ParquetStore) -> None:
    store.write_partition(
        DIVIDENDS_JOB.dataset,
        "TW",
        date(2026, 10, 20),
        [
            {
                "symbol": "2330",
                "market": "TW",
                "announced_date": "2026-08-12",
                "fiscal_year": "2026Q2",
                "cash_dividend": 5.0,
                "stock_dividend": 0.0,
                "cash_ex_dividend_date": "2026-10-28",
                "stock_ex_dividend_date": None,
                "cash_dividend_payment_date": "2026-11-12",
            },
            {  # outside the window
                "symbol": "2317",
                "market": "TW",
                "announced_date": "2026-08-12",
                "fiscal_year": "2026Q2",
                "cash_dividend": 1.0,
                "stock_dividend": 0.0,
                "cash_ex_dividend_date": "2026-11-20",
                "stock_ex_dividend_date": None,
                "cash_dividend_payment_date": None,
            },
        ],
    )
    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        FRIDAY,
        [
            {
                "symbol": "2317",
                "market": "TW",
                "company_name": "鴻海",
                "announced_date": "2026-10-22",
                "reason": "連續三次",
                "period_start": "2026-10-27",
                "period_end": "2026-11-09",
                "measure": "第一次處置",
                "detail": None,
                "security_type": "twse",
            }
        ],
    )
    store.write_partition(
        ATTENTION_STOCKS_DATASET,
        "TW",
        FRIDAY,
        [
            {
                "symbol": "6999",
                "market": "TW",
                "company_name": "新股",
                "flagged_date": "2026-10-23",
                "reason": "x",
                "close": 50.0,
                "pe": None,
                "security_type": "tpex",
            }
        ],
    )


async def _no_feed(*_: object, **__: object) -> tuple[tuple[CalendarEvent, ...], object]:
    return (), SimpleNamespace(health=SourceHealth(status=HealthStatus.OK, detail="stub"))


@pytest.fixture(autouse=True)
def _stub_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "fetch_feed_events", _no_feed)


def _tool(
    tmp_path: Path, reader: FakeReader, *, now: datetime = NOW
) -> GetWatchlistWeeklyReviewTool:
    return GetWatchlistWeeklyReviewTool(
        reader,  # type: ignore[arg-type]
        ParquetStore(root=tmp_path),
        object(),  # type: ignore[arg-type]  # the router: only the stubbed feed would use it
        clock=lambda: now,
    )


async def _run(
    tool: GetWatchlistWeeklyReviewTool, **args: object
) -> GetWatchlistWeeklyReviewOutput:
    out = await tool.run(GetWatchlistWeeklyReviewInput.model_validate(args), ALICE)
    assert isinstance(out, GetWatchlistWeeklyReviewOutput)
    return out


# ---- the happy path -------------------------------------------------------------------------


async def test_reviews_both_markets_from_the_stored_data(tmp_path: Path) -> None:
    _seed_week(ParquetStore(root=tmp_path))
    _seed_events(ParquetStore(root=tmp_path))
    reader = FakeReader(
        _snapshot(
            _group("台股群", Market.TW, ["2330", "2317", "6999"]),
            _group("美股群", Market.US, ["AAPL", "MSFT"]),
        )
    )
    out = await _run(_tool(tmp_path, reader))

    assert out.nothing_to_review is False
    assert out.review.symbols_total == 5
    assert out.review.symbols_covered == 5
    assert out.review.groups == 2
    assert [s.market for s in out.review.markets] == [Market.TW, Market.US]
    tw, us = out.review.markets
    moves = {m.symbol: m for m in tw.moves}
    assert moves["2330"].change_pct == 3.0
    assert moves["2317"].change_pct == -5.0
    assert moves["6999"].status == "no_base_price"
    assert {m.symbol: m.change_pct for m in us.moves} == {"AAPL": 2.5, "MSFT": 0.0}
    assert tw.freshness.data_as_of == FRIDAY
    assert not tw.freshness.stale
    assert tw.freshness.base_date == PREV_FRIDAY

    report = out.report
    assert "### 台股（3 檔）" in report
    assert "### 美股（2 檔）" in report
    assert "漲幅居前：2330 台積電 +3.00%（1,000.00 → 1,030.00）" in report
    assert "跌幅居前：2317 -5.00%" in report
    assert "6999（前一週沒有收盤價（可能是新掛牌））" in report
    assert "2026-10-28 2330 台積電 除息，現金股利每股 5（來源：股利公告資料）" in report
    assert "2026-10-27 2317 處置期間開始" in report
    assert "2317 處置期間中（自 2026-10-27" not in report  # not yet started on the data date
    assert "6999 近期列入注意股" in report
    assert "2026-11-20" not in report  # outside the seven-day window
    assert out.review.window_start == date(2026, 10, 24)
    assert out.review.window_end == date(2026, 10, 30)
    assert out.health_status is HealthStatus.STALE  # the US dividend dataset has nothing stored
    assert any("股利資料" in c for c in out.caveats)
    assert out.currentness_caveat
    assert reader.users == ["alice"]


async def test_the_data_comes_from_the_caller_only(tmp_path: Path) -> None:
    reader = FakeReader(_snapshot())
    await _run(_tool(tmp_path, reader))
    assert reader.users == ["alice"]
    with pytest.raises(ValidationError):
        GetWatchlistWeeklyReviewInput.model_validate({"user_id": "bob"})


async def test_an_anonymous_caller_gets_the_sign_in_result(tmp_path: Path) -> None:
    result = await dispatch_tool(
        _tool(tmp_path, FakeReader(_snapshot())), GetWatchlistWeeklyReviewInput(), ToolContext()
    )
    assert isinstance(result, SignInRequired)


def test_it_is_a_private_tool() -> None:
    assert GetWatchlistWeeklyReviewTool.data_class is DataClass.PRIVATE


# ---- empty, stale, unreachable ----------------------------------------------------------------


async def test_a_user_with_no_symbols_gets_the_calm_one_liner(tmp_path: Path) -> None:
    out = await _run(_tool(tmp_path, FakeReader(_snapshot())))
    assert out.nothing_to_review is True
    assert out.report.splitlines()[0] == "本週沒有可回顧的自選股：你的自選股群組目前是空的。"
    assert out.health_status is HealthStatus.OK
    assert out.review.markets == ()


async def test_stale_prices_are_flagged_in_the_report_and_the_health(tmp_path: Path) -> None:
    _seed_week(ParquetStore(root=tmp_path), last=date(2026, 10, 21))  # Wednesday is the newest
    reader = FakeReader(_snapshot(_group("台股群", Market.TW, ["2330"])))
    out = await _run(_tool(tmp_path, reader))
    tw = out.review.markets[0]
    assert tw.freshness.stale is True
    assert tw.freshness.data_as_of == date(2026, 10, 21)
    assert tw.freshness.expected_as_of == FRIDAY
    assert "較預期的 2026-10-23 舊" in out.report
    assert out.health_status is HealthStatus.STALE
    assert any("older than the expected trading day" in c for c in out.caveats)


async def test_a_market_with_no_stored_prices_is_unavailable_and_says_so(tmp_path: Path) -> None:
    reader = FakeReader(_snapshot(_group("美股群", Market.US, ["AAPL"])))
    out = await _run(_tool(tmp_path, reader))
    assert out.health_status is HealthStatus.UNAVAILABLE
    assert "資料日期：目前沒有可用的收盤資料。" in out.report
    assert "AAPL（查無收盤資料）" in out.report


async def test_an_unreachable_calendar_feed_is_reported_not_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(*_: object, **__: object) -> tuple[tuple[CalendarEvent, ...], object]:
        raise TimeoutError

    monkeypatch.setattr(module, "fetch_feed_events", down)
    _seed_week(ParquetStore(root=tmp_path))
    _seed_events(ParquetStore(root=tmp_path))
    reader = FakeReader(_snapshot(_group("台股群", Market.TW, ["2330"])))
    out = await _run(_tool(tmp_path, reader))
    assert "交易所行事曆暫時無法讀取（部分查詢逾時或失敗）" in out.report
    assert out.health_status is HealthStatus.STALE
    assert "2026-10-28 2330 台積電 除息" in out.report  # the stored events are still there


async def test_an_unhealthy_feed_answer_counts_as_incomplete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def unhealthy(*_: object, **__: object) -> tuple[tuple[CalendarEvent, ...], object]:
        return (), SimpleNamespace(
            health=SourceHealth(status=HealthStatus.UNREACHABLE, detail="stub")
        )

    monkeypatch.setattr(module, "fetch_feed_events", unhealthy)
    _seed_week(ParquetStore(root=tmp_path))
    out = await _run(_tool(tmp_path, FakeReader(_snapshot(_group("g", Market.TW, ["2330"])))))
    assert "交易所行事曆暫時無法讀取" in out.report


async def test_feed_events_are_shown_once_next_to_the_stored_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = (
        CalendarEvent(
            id="a",
            market=Market.TW,
            stock_id="2330",
            type=EventType.AGM,
            title_zh="股東會",
            title_en="AGM",
            event_date=date(2026, 10, 29),
            state=EventState.SCHEDULED,
            source="twse",
        ),
        CalendarEvent(  # the same ex-date the stored dividend rows already list
            id="b",
            market=Market.TW,
            stock_id="2330",
            type=EventType.DIVIDEND_EX_RIGHTS,
            title_zh="除權息",
            title_en="Ex",
            event_date=date(2026, 10, 28),
            state=EventState.SCHEDULED,
            source="twse",
        ),
    )

    async def served(*_: object, **__: object) -> tuple[tuple[CalendarEvent, ...], object]:
        return feed, SimpleNamespace(health=SourceHealth(status=HealthStatus.OK, detail="stub"))

    monkeypatch.setattr(module, "fetch_feed_events", served)
    _seed_week(ParquetStore(root=tmp_path))
    _seed_events(ParquetStore(root=tmp_path))
    out = await _run(_tool(tmp_path, FakeReader(_snapshot(_group("g", Market.TW, ["2330"])))))
    assert out.report.count("2026-10-28 2330 台積電") == 1
    assert "2026-10-29 2330 台積電 股東會（來源：交易所行事曆）" in out.report


# ---- scope ------------------------------------------------------------------------------------


async def test_the_symbol_cap_reports_the_true_count(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    symbols = [str(1100 + n) for n in range(MAX_REVIEW_SYMBOLS + 15)]
    days = _weekdays(date(2026, 10, 12), FRIDAY)
    _seed_prices(store, Market.TW, {s: dict.fromkeys(days, 10.0) for s in symbols}, days)
    reader = FakeReader(_snapshot(_group("big", Market.TW, symbols)))
    out = await _run(_tool(tmp_path, reader))
    assert out.review.symbols_total == MAX_REVIEW_SYMBOLS + 15
    assert out.review.symbols_covered == MAX_REVIEW_SYMBOLS
    assert len(out.review.markets[0].moves) == MAX_REVIEW_SYMBOLS
    assert "超過單次上限而未納入" in out.report
    assert any("only 60 were reviewed" in c for c in out.caveats)


async def test_a_symbol_in_two_groups_is_counted_once(tmp_path: Path) -> None:
    _seed_week(ParquetStore(root=tmp_path))
    reader = FakeReader(
        _snapshot(_group("a", Market.TW, ["2330", "2317"]), _group("b", Market.TW, ["2330"]))
    )
    out = await _run(_tool(tmp_path, reader))
    assert out.review.symbols_total == 2
    assert out.review.groups == 2


async def test_group_and_market_filters(tmp_path: Path) -> None:
    _seed_week(ParquetStore(root=tmp_path))
    reader = FakeReader(
        _snapshot(_group("核心", Market.TW, ["2330"]), _group("美股群", Market.US, ["AAPL"]))
    )
    out = await _run(_tool(tmp_path, reader), group="核心")
    assert out.review.symbols_total == 1
    assert [s.market for s in out.review.markets] == [Market.TW]
    await _run(_tool(tmp_path, reader), market="US")
    assert reader.calls[-1]["market"] is Market.US


async def test_top_n_limits_the_lists(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    days = _weekdays(date(2026, 10, 12), FRIDAY)
    closes = {
        str(2000 + n): {d: 100.0 + (n if d == FRIDAY else 0.0) for d in days} for n in range(1, 6)
    }
    _seed_prices(store, Market.TW, closes, days)
    out = await _run(
        _tool(tmp_path, FakeReader(_snapshot(_group("g", Market.TW, list(closes))))), top_n=2
    )
    line = next(ln for ln in out.report.splitlines() if ln.startswith("- 漲幅居前"))
    assert line.count("%（") == 2


# ---- helpers ----------------------------------------------------------------------------------


def test_expected_trading_day_waits_for_the_data_to_land() -> None:
    def weekdays(day: date) -> bool:
        return day.weekday() < 5

    wed_morning = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
    wed_evening = datetime(2026, 10, 21, 19, 0, tzinfo=UTC)
    sat = datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
    assert expected_trading_day(weekdays, wed_morning) == date(2026, 10, 20)
    assert expected_trading_day(weekdays, wed_evening) == date(2026, 10, 21)
    assert expected_trading_day(weekdays, sat) == FRIDAY
    # A closed Friday (holiday or a confirmed closure) moves the expectation back to Thursday.
    assert expected_trading_day(lambda d: weekdays(d) and d != FRIDAY, sat) == date(2026, 10, 22)


async def test_a_confirmed_closure_is_not_stale_but_a_missing_day_is(tmp_path: Path) -> None:
    store = ParquetStore(root=tmp_path)
    _seed_week(store, last=date(2026, 10, 22))  # Friday 23rd has no partition
    reader = FakeReader(_snapshot(_group("g", Market.TW, ["2330"])))
    assert (await _run(_tool(tmp_path, reader))).review.markets[0].freshness.stale is True
    store.mark_closed(PRICE_EOD_JOB.dataset, "TW", FRIDAY, "typhoon closure")
    fresh = (await _run(_tool(tmp_path, reader))).review.markets[0].freshness
    assert fresh.stale is False
    assert fresh.expected_as_of == date(2026, 10, 22)


async def test_the_cap_is_shared_between_the_markets(tmp_path: Path) -> None:
    tw = [str(1100 + n) for n in range(59)]
    us = [f"US{n}" for n in range(40)]
    reader = FakeReader(_snapshot(_group("tw", Market.TW, tw), _group("us", Market.US, us)))
    out = await _run(_tool(tmp_path, reader))
    assert out.review.symbols_total == 99
    assert [(s.market, s.symbols_covered) for s in out.review.markets] == [
        (Market.TW, 30),
        (Market.US, 30),
    ]


async def test_a_small_market_hands_its_room_to_the_other(tmp_path: Path) -> None:
    tw = [str(1100 + n) for n in range(80)]
    reader = FakeReader(_snapshot(_group("tw", Market.TW, tw), _group("us", Market.US, ["AAPL"])))
    out = await _run(_tool(tmp_path, reader))
    assert [(s.market, s.symbols_covered) for s in out.review.markets] == [
        (Market.TW, 59),
        (Market.US, 1),
    ]


async def test_an_attention_listing_seen_twice_is_shown_once_with_the_newest_date(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    _seed_week(store)
    _seed_events(store)
    flag = {
        "symbol": "6999",
        "market": "TW",
        "company_name": "新股",
        "reason": "x",
        "close": 50.0,
        "pe": None,
        "security_type": "tpex",
    }
    store.write_partition(
        ATTENTION_STOCKS_DATASET,
        "TW",
        FRIDAY,
        [{**flag, "flagged_date": "2026-10-22"}, {**flag, "flagged_date": "2026-10-23"}],
    )
    out = await _run(_tool(tmp_path, FakeReader(_snapshot(_group("g", Market.TW, ["6999"])))))
    assert out.report.count("6999 近期列入注意股") == 1
    assert "公告日 2026-10-23" in out.report


async def test_a_disposition_that_ended_before_the_run_is_not_reported_as_current(
    tmp_path: Path,
) -> None:
    store = ParquetStore(root=tmp_path)
    _seed_week(store)
    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        FRIDAY,
        [
            {
                "symbol": "2317",
                "market": "TW",
                "company_name": "鴻海",
                "announced_date": "2026-10-14",
                "reason": "x",
                "period_start": "2026-10-15",
                "period_end": "2026-10-23",
                "measure": "m",
                "detail": None,
                "security_type": "twse",
            }
        ],
    )
    out = await _run(_tool(tmp_path, FakeReader(_snapshot(_group("g", Market.TW, ["2317"])))))
    assert "處置期間中" not in out.report


def test_feed_events_without_a_date_or_symbol_are_skipped() -> None:
    market_wide = CalendarEvent(
        id="c",
        market=Market.TW,
        type=EventType.AGM,
        title_zh="x",
        title_en="x",
        event_date=date(2026, 10, 29),
        state=EventState.SCHEDULED,
        source="twse",
    )
    closure = CalendarEvent(
        id="d",
        market=Market.TW,
        stock_id="2330",
        type=EventType.MARKET_CLOSURE,
        title_zh="x",
        title_en="x",
        event_date=date(2026, 10, 29),
        state=EventState.SCHEDULED,
        source="ncdr",
    )
    assert feed_events_to_review((market_wide, closure), {}, set()) == []
