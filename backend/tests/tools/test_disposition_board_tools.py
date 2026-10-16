"""`GetDispositionBoardTool` — bucket classification (風險股/處置中/即將出關), rolling-window
projection, and the whole board for every caller (it is built on free exchange data)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from ingest.jobs import ATTENTION_STOCKS_DATASET, DISPOSITION_EVENTS_DATASET, PRICE_EOD_JOB
from ingest.store import ParquetStore
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.disposition_board_tools import (
    GetDispositionBoardInput,
    GetDispositionBoardOutput,
    GetDispositionBoardTool,
)

_MARKET = Market.TW
_REPORT_DATE = date(2026, 9, 4)
_PRIOR_DATE = date(2026, 9, 3)


def _price_rows(
    symbol: str, close: float, prior_close: float
) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "symbol": symbol,
            "market": "TW",
            "trade_date": _REPORT_DATE.isoformat(),
            "open": prior_close,
            "high": close,
            "low": prior_close,
            "close": close,
            "volume": 1000,
            "turnover": close * 1000,
        },
        {
            "symbol": symbol,
            "market": "TW",
            "trade_date": _PRIOR_DATE.isoformat(),
            "open": prior_close,
            "high": prior_close,
            "low": prior_close,
            "close": prior_close,
            "volume": 900,
            "turnover": prior_close * 900,
        },
    )


def _seed_store(tmp_path: object) -> ParquetStore:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]

    disposed_today, disposed_prior = _price_rows("3008", 2500.0, 2400.0)
    at_risk_today, at_risk_prior = _price_rows("3081", 3200.0, 3170.0)
    store.write_partition(
        PRICE_EOD_JOB.dataset, "TW", _REPORT_DATE, [disposed_today, at_risk_today]
    )
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", _PRIOR_DATE, [disposed_prior, at_risk_prior])

    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": "3008",
                "market": "TW",
                "company_name": "大立光",
                "announced_date": "2026-09-02",
                "reason": "連續三次",
                "period_start": "2026-09-03",
                "period_end": "2026-09-20",
                "measure": "第一次處置",
                "detail": None,
                "security_type": "twse",
            }
        ],
    )
    store.write_partition(
        ATTENTION_STOCKS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": "3081",
                "market": "TW",
                "company_name": "聯亞",
                "flagged_date": "2026-09-04",
                "reason": "當日本益比為272.81(第六款)",
                "close": 3200.0,
                "pe": 272.81,
                "security_type": "twse",
            }
        ],
    )
    return store


def _tool(
    store: ParquetStore,
    day_trading_volume: int | None = None,
    day_trading_trade_date: date | None = None,
    self_assessed_rows: list[dict[str, object]] | None = None,
) -> GetDispositionBoardTool:
    results: dict[Capability, SourceResult] = {}
    if day_trading_volume is not None:
        results[Capability.DAY_TRADING] = SourceResult(
            source_id="STUB",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub day-trading caveat",
            payload={
                "rows": [
                    {
                        "symbol": "3008",
                        "market": "TW",
                        "trade_date": (day_trading_trade_date or _REPORT_DATE).isoformat(),
                        "buy_after_sale": "",
                        "volume": day_trading_volume,
                        "buy_amount": 0,
                        "sell_amount": 0,
                    }
                ]
            },
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )
    if self_assessed_rows is not None:
        results[Capability.SELF_ASSESSED_EARNINGS] = SourceResult(
            source_id="STUB",
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.REFERENCE,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub self-assessed-earnings caveat",
            payload={"rows": self_assessed_rows},
            health=SourceHealth(status=HealthStatus.OK, detail="ok"),
        )
    router, _ = make_router(Symbol(id="3008", market=_MARKET), results)
    return GetDispositionBoardTool(store, router)


async def _run(tool: GetDispositionBoardTool) -> GetDispositionBoardOutput:
    ctx = ToolContext(tls_policy=TLSPolicy.STRICT)
    result = await tool.run(GetDispositionBoardInput(market=_MARKET), ctx)
    assert isinstance(result, GetDispositionBoardOutput)
    return result


async def test_bucket_classification_and_prices(tmp_path: object) -> None:
    store = _seed_store(tmp_path)

    output = await _run(_tool(store))

    assert output.data_available
    assert output.report_date == _REPORT_DATE
    cards_by_symbol = {c.symbol: c for c in output.cards}
    assert cards_by_symbol["3008"].bucket == "in_disposition"
    assert cards_by_symbol["3081"].bucket == "at_risk"
    assert cards_by_symbol["3008"].disposition_period_end == date(2026, 9, 20)
    assert cards_by_symbol["3008"].close == 2500.0
    assert cards_by_symbol["3081"].close == 3200.0
    assert output.bucket_counts == {"in_disposition": 1, "at_risk": 1}
    # An at_risk symbol has no `DispositionEventRow` of its own — its name/exchange must fall
    # back to its `AttentionStockRow`, not silently render blank (a real gap this test guards).
    assert cards_by_symbol["3081"].company_name == "聯亞"
    assert cards_by_symbol["3081"].security_type == "twse"


async def test_day_trading_ratio_and_days_until_exiting(tmp_path: object) -> None:
    store = _seed_store(tmp_path)

    output = await _run(_tool(store, day_trading_volume=250))

    card = next(c for c in output.cards if c.symbol == "3008")
    assert card.volume == 1000
    assert card.day_trading_ratio == 25.0
    assert "沖" in card.flags
    # period_end 2026-09-20 is 16 calendar days after report_date 2026-09-04 — comfortably
    # outside the 5-day exiting-soon window, so this only asserts the field is populated and
    # positive, not an exact business-day count (calendar-day/business-day math is already
    # covered by `TradingDayCalendar`'s own tests).
    assert card.days_until_exiting is not None
    assert card.days_until_exiting > 0


async def test_day_trading_ratio_ignores_stale_row_not_matching_price_date(
    tmp_path: object,
) -> None:
    """A day-trading fetch can return its own latest row from a DIFFERENT date than the price
    join actually resolved to (real upstream cadence mismatch, seen live: >100%/up to ~800%
    ratios when a stale day-trading volume was paired with a newer total volume) — only a row
    whose own `trade_date` matches the resolved price date may be used."""
    store = _seed_store(tmp_path)
    stale_date = date(2026, 8, 20)

    output = await _run(_tool(store, day_trading_volume=900, day_trading_trade_date=stale_date))

    card = next(c for c in output.cards if c.symbol == "3008")
    assert card.day_trading_ratio is None
    assert "沖" not in card.flags


async def test_estimated_days_until_disposition_extrapolates_observed_rate(
    tmp_path: object,
) -> None:
    """`_seed_store` flags "3081" once on the only observed day (`_REPORT_DATE`) — an observed
    rate of 1 flag/day. With `_ATTENTION_FLAGS_TO_DISPOSITION = 3`, 2 more flags are needed;
    at a 1/day rate that is 2 more trading days, not the raw flag count itself."""
    store = _seed_store(tmp_path)

    output = await _run(_tool(store))

    card = next(c for c in output.cards if c.symbol == "3081")
    assert card.attention_flags_remaining == 2
    assert card.estimated_days_until_disposition == 2


async def test_days_until_exiting_is_zero_on_period_end_day(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    disposed_today, disposed_prior = _price_rows("3008", 2500.0, 2400.0)
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", _REPORT_DATE, [disposed_today])
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", _PRIOR_DATE, [disposed_prior])
    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": "3008",
                "market": "TW",
                "company_name": "大立光",
                "announced_date": "2026-09-02",
                "reason": "連續三次",
                "period_start": "2026-08-31",
                "period_end": "2026-09-04",
                "measure": "第一次處置",
                "detail": None,
                "security_type": "twse",
            }
        ],
    )

    output = await _run(_tool(store))

    card = output.cards[0]
    assert card.days_until_exiting == 0


async def test_price_join_falls_back_when_price_eod_lags_report_date(tmp_path: object) -> None:
    """`disposition_events` can report a later date than `PRICE_EOD_JOB` has actually ingested
    (sparse dev backfill, or a slower-cadence price pull) — the join must fall back to the latest
    real price partition at or before the report date, not silently return blank price/change."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    lagging_price_date = date(2026, 9, 1)
    lagging_prior_date = date(2026, 8, 31)
    today, prior = _price_rows("3008", 2500.0, 2400.0)
    today["trade_date"] = lagging_price_date.isoformat()
    prior["trade_date"] = lagging_prior_date.isoformat()
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", lagging_price_date, [today])
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", lagging_prior_date, [prior])
    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": "3008",
                "market": "TW",
                "company_name": "大立光",
                "announced_date": "2026-09-02",
                "reason": "連續三次",
                "period_start": "2026-09-03",
                "period_end": "2026-09-20",
                "measure": "第一次處置",
                "detail": None,
                "security_type": "twse",
            }
        ],
    )

    output = await _run(_tool(store))

    card = output.cards[0]
    assert card.close == 2500.0
    assert card.change_pct is not None


async def test_matching_interval_minutes_parsed_from_detail(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": "3664",
                "market": "TW",
                "company_name": "安瑞科技",
                "announced_date": "2026-09-04",
                "reason": "連續三個營業日達注意標準",
                "period_start": "2026-09-04",
                "period_end": "2026-09-10",
                "measure": "第一次處置",
                "detail": "改以人工管制之撮合終端機執行撮合作業(約每45分鐘撮合一次)",
                "security_type": "twse",
            },
            {
                "symbol": "3008",
                "market": "TW",
                "company_name": "大立光",
                "announced_date": "2026-09-02",
                "reason": "連續三次",
                "period_start": "2026-09-03",
                "period_end": "2026-09-20",
                "measure": "約每二分鐘撮合一次",
                "detail": None,
                "security_type": "twse",
            },
        ],
    )

    output = await _run(_tool(store))

    cards_by_symbol = {c.symbol: c for c in output.cards}
    assert cards_by_symbol["3664"].matching_interval_minutes == 45
    assert cards_by_symbol["3008"].matching_interval_minutes == 2


async def test_exiting_soon_bucket_when_period_end_is_close(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    disposed_today, disposed_prior = _price_rows("3008", 2500.0, 2400.0)
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", _REPORT_DATE, [disposed_today])
    store.write_partition(PRICE_EOD_JOB.dataset, "TW", _PRIOR_DATE, [disposed_prior])
    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": "3008",
                "market": "TW",
                "company_name": "大立光",
                "announced_date": "2026-09-02",
                "reason": "連續三次",
                "period_start": "2026-09-03",
                "period_end": "2026-09-09",
                "measure": "第一次處置",
                "detail": None,
                "security_type": "twse",
            }
        ],
    )

    output = await _run(_tool(store))

    cards_by_symbol = {c.symbol: c for c in output.cards}
    assert cards_by_symbol["3008"].bucket == "exiting_soon"


async def test_self_assessed_earnings_prefers_consolidated_row(tmp_path: object) -> None:
    store = _seed_store(tmp_path)
    rows = [
        {
            "symbol": "3008",
            "market": "TW",
            "company_name": "大立光",
            "revenue_period": "2026-08",
            "consolidated": False,
            "monthly_operating_profit": 1,
            "ytd_operating_profit": 2,
            "monthly_pretax_profit": 3,
            "ytd_pretax_profit": 4,
            "variance_note": None,
        },
        {
            "symbol": "3008",
            "market": "TW",
            "company_name": "大立光",
            "revenue_period": "2026-08",
            "consolidated": True,
            "monthly_operating_profit": 100,
            "ytd_operating_profit": 200,
            "monthly_pretax_profit": 300,
            "ytd_pretax_profit": 400,
            "variance_note": None,
        },
    ]

    output = await _run(_tool(store, self_assessed_rows=rows))

    card = next(c for c in output.cards if c.symbol == "3008")
    assert card.self_assessed_period == "2026-08"
    assert card.self_assessed_consolidated is True
    assert card.self_assessed_monthly_pretax_profit == 300
    assert card.self_assessed_ytd_pretax_profit == 400


async def test_self_assessed_earnings_absent_when_no_filing(tmp_path: object) -> None:
    store = _seed_store(tmp_path)

    output = await _run(_tool(store))

    card = next(c for c in output.cards if c.symbol == "3008")
    assert card.self_assessed_period is None
    assert card.self_assessed_monthly_pretax_profit is None


async def test_projection_caveat_always_populated(tmp_path: object) -> None:
    store = _seed_store(tmp_path)

    output = await _run(_tool(store))

    assert output.cards
    for card in output.cards:
        assert card.projection_caveat


async def test_no_ingested_data_returns_unavailable(tmp_path: object) -> None:
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]

    output = await _run(_tool(store))

    assert output.data_available is False
    assert output.unavailable_reason is not None
    assert output.cards == ()


def test_data_class_is_public(tmp_path: object) -> None:
    """Every input is free exchange / MOPS open data, so the board is not a Sponsor feature."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]

    assert _tool(store).data_class == DataClass.PUBLIC


async def test_free_caller_sees_every_card_ranked_per_bucket(tmp_path: object) -> None:
    """A FREE caller gets every card real, each ranked 1.. within its own bucket."""
    store = ParquetStore(root=tmp_path)  # type: ignore[arg-type]
    # `list_partition_dates` ignores a genuinely-empty write (see its own docstring) — a real
    # row (for a symbol distinct from the at_risk roster below) is needed to make this date
    # resolve as the report date at all.
    store.write_partition(
        DISPOSITION_EVENTS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": "9999",
                "market": "TW",
                "company_name": "測試股",
                "announced_date": "2026-09-02",
                "reason": "連續三次",
                "period_start": "2026-09-03",
                "period_end": "2026-09-20",
                "measure": "第一次處置",
                "detail": None,
                "security_type": "twse",
            }
        ],
    )
    store.write_partition(
        ATTENTION_STOCKS_DATASET,
        "TW",
        _REPORT_DATE,
        [
            {
                "symbol": symbol,
                "market": "TW",
                "company_name": name,
                "flagged_date": _REPORT_DATE.isoformat(),
                "reason": "當日本益比為272.81(第六款)",
                "close": 100.0,
                "pe": 20.0,
                "security_type": "twse",
            }
            for symbol, name in [("1101", "台泥"), ("1102", "亞泥"), ("1103", "嘉泥")]
        ],
    )
    output = await _tool(store).run(GetDispositionBoardInput(market=_MARKET), ToolContext())

    assert output.data_available is True
    at_risk_cards = [c for c in output.cards if c.bucket == "at_risk"]
    assert [c.rank for c in at_risk_cards] == [1, 2, 3]
    assert [c.symbol for c in at_risk_cards] == ["1101", "1102", "1103"]
    assert all(c.company_name is not None for c in at_risk_cards)
