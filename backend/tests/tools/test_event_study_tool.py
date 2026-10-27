"""`get_event_study`: the typed output over a small synthetic store, the free/paid tiers, the
capital-reduction (per-symbol) path, the governance text scan and the chart hint."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from analytics.event_study.loader import (
    ATTENTION_STOCKS_DATASET,
    DISPOSITION_EVENTS_DATASET,
    PRICE_EOD_DATASET,
)
from governance.textscan import scan_structured_payload
from ingest.store import ParquetStore
from schemas.chart_hint import Mark
from schemas.enums import Capability, DataClass, Market, Tier, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import MaskedTeaser, ToolContext, dispatch_tool
from tools.market.event_study import (
    GetEventStudyInput,
    GetEventStudyOutput,
    GetEventStudyTool,
    _health,
)

_N_STOCKS = 35  # event stocks 1101..1135, enough to clear the 30-event floor
_N_FILLER = 120  # extra stocks so the market proxy has a breadth to take a median over
_FIRST_DAY = date(2026, 6, 1)
_N_DAYS = 70
_EVENT_INDEX = 20  # day index of the disposition start / attention listing in the synthetic panel


def _weekdays(first: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = first
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def _symbols() -> list[str]:
    return [f"{1100 + k}" for k in range(1, _N_STOCKS + 1)]


def _build_store(tmp_path: Path) -> tuple[ParquetStore, list[date]]:
    """70 weekdays of prices; event stock k closes at 100 until day 20, then 100 + k (so its
    5- and 20-day return from day 20 is k percent); fillers stay at 100."""
    store = ParquetStore(root=tmp_path)
    days = _weekdays(_FIRST_DAY, _N_DAYS)
    for index, day in enumerate(days):
        rows: list[dict[str, object]] = []
        for k, symbol in enumerate(_symbols(), start=1):
            close = 100.0 if index <= _EVENT_INDEX else 100.0 + k
            rows.append(
                {"symbol": symbol, "trade_date": day.isoformat(), "close": close, "volume": 10_000}
            )
        rows.extend(
            {
                "symbol": f"{5000 + j}",
                "trade_date": day.isoformat(),
                "close": 50.0,
                "volume": 10_000,
            }
            for j in range(_N_FILLER)
        )
        rows.append({"symbol": "086845", "trade_date": day.isoformat(), "close": 1.0, "volume": 1})
        store.write_partition(PRICE_EOD_DATASET, "TW", day, rows)
    return store, days


def _disposition_row(symbol: str, start: date, end: date) -> dict[str, object]:
    return {
        "symbol": symbol,
        "market": "TW",
        "company_name": f"公司{symbol}",
        "announced_date": start - timedelta(days=1),
        "reason": "連續三次",
        "period_start": start,
        "period_end": end,
        "measure": "第一次處置",
        "detail": None,
        "security_type": "twse",
    }


def _with_dispositions(tmp_path: Path) -> tuple[ParquetStore, list[date]]:
    store, days = _build_store(tmp_path)
    start = days[_EVENT_INDEX]
    rows = [_disposition_row(s, start, days[_EVENT_INDEX + 4]) for s in _symbols()]
    rows.append(_disposition_row("086845", start, days[_EVENT_INDEX + 4]))  # a warrant
    store.write_partition(DISPOSITION_EVENTS_DATASET, "TW", days[_EVENT_INDEX + 1], rows)
    store.write_partition(DISPOSITION_EVENTS_DATASET, "TW", days[_EVENT_INDEX + 2], rows)
    return store, days


def _tool(store: ParquetStore) -> GetEventStudyTool:
    router, _adapter = make_router(Symbol(id="1101", market=Market.TW), {})
    return GetEventStudyTool(store, router)


async def _run(
    tool: GetEventStudyTool, tier: Tier = Tier.FREE, **kwargs: object
) -> GetEventStudyOutput:
    args = GetEventStudyInput.model_validate(kwargs)
    result = await dispatch_tool(tool, args, ToolContext(tier=tier))
    assert isinstance(result, GetEventStudyOutput)
    return result


async def test_disposition_start_reports_counts_statistics_and_the_chart(tmp_path: Path) -> None:
    store, days = _with_dispositions(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start")

    assert out.data_available is True
    assert (
        out.counts.candidates == _N_STOCKS
    )  # the warrant is excluded, repeated snapshots collapse
    assert out.counts.excluded_non_equity == 2
    five = next(h for h in out.horizons if h.horizon_days == 5)
    assert five.status == "ok" and five.n_events == _N_STOCKS and five.n_symbols == _N_STOCKS
    assert five.n_event_days == 1 and five.max_events_on_one_day == _N_STOCKS
    assert five.mean_pct == pytest.approx(18.0)  # mean of 1..35 percent
    assert five.median_pct == pytest.approx(18.0)
    assert five.share_positive_pct == pytest.approx(100.0)
    assert five.mean_excess_pct == pytest.approx(18.0)  # fillers are flat: the market median is 0
    twenty = next(h for h in out.horizons if h.horizon_days == 20)
    assert twenty.status == "ok"
    sixty = next(h for h in out.horizons if h.horizon_days == 60)
    assert sixty.status == "insufficient_sample"
    assert sixty.n_events == 0 and sixty.n_censored == _N_STOCKS
    assert sixty.mean_pct is None and sixty.median_excess_pct is None
    assert out.data_window.latest_price_date == days[-1]
    assert out.data_window.price_window_start == days[0]  # the store's history, not the read window
    assert out.data_window.event_feed_snapshots == 2
    assert out.proxy.id == "median_all_tw_stocks"


async def test_the_chart_plots_excess_return_by_horizon_with_n_in_the_label(tmp_path: Path) -> None:
    store, _days = _with_dispositions(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start")

    hint = out.chart_hint
    assert hint is not None and hint.mark is Mark.BAR and hint.data_key == "chart_rows"
    assert hint.x is not None and hint.x.field == "label"
    assert hint.y is not None and hint.y.field == "excess_return_pct"
    assert hint.color is not None and hint.color.field == "statistic"
    labels = {row.label for row in out.chart_rows}
    assert labels == {f"D+5 (n={_N_STOCKS})", f"D+20 (n={_N_STOCKS})"}  # no bar for D+60
    assert {row.statistic for row in out.chart_rows} == {"mean", "median"}


async def test_free_and_paid_callers_get_the_same_full_result(tmp_path: Path) -> None:
    store, _days = _with_dispositions(tmp_path)
    tool = _tool(store)

    free = await _run(tool, Tier.FREE, event_type="disposition_start")
    premium = await _run(tool, Tier.PREMIUM, event_type="disposition_start")

    assert tool.data_class is DataClass.PUBLIC
    assert free == premium
    assert not isinstance(free, MaskedTeaser)
    assert free.horizons[0].mean_pct is not None


async def test_the_output_passes_the_governance_text_scan(tmp_path: Path) -> None:
    store, _days = _with_dispositions(tmp_path)
    tool = _tool(store)
    for event_type in ("disposition_start", "disposition_end", "attention_listed", "buyback_start"):
        out = await _run(tool, event_type=event_type)
        payload = out.model_dump(mode="json")

        cleaned, redacted = scan_structured_payload(payload)

        assert redacted is False, event_type
        assert cleaned == payload, event_type


async def test_the_static_texts_pass_the_scan_for_every_event_type_and_the_capital_path(
    tmp_path: Path,
) -> None:
    store, _days = _with_dispositions(tmp_path)
    tool = _tool(store)
    for event_type in ("capital_reduction_resume", "buyback_end"):
        out = await _run(tool, event_type=event_type)  # unavailable shapes carry the same texts
        cleaned, redacted = scan_structured_payload(out.model_dump(mode="json"))
        assert redacted is False and cleaned == out.model_dump(mode="json")


async def test_limitations_are_typed_and_say_what_the_sample_cannot_support(tmp_path: Path) -> None:
    store, days = _with_dispositions(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start")

    codes = {item.code for item in out.limitations}
    assert {
        "short_price_history",
        "short_event_history",
        "insufficient_sample",
        "event_clustering",
        "survivorship",
        "unadjusted_prices",
        "no_costs",
        "not_predictive",
    } <= codes
    history = next(i for i in out.limitations if i.code == "short_event_history")
    assert days[_EVENT_INDEX + 1].isoformat() in history.text
    clustering = next(i for i in out.limitations if i.code == "event_clustering")
    assert f"{_N_STOCKS} events fall on 1 distinct days" in clustering.text
    assert all(i.text and i.text_zh for i in out.limitations)


async def test_a_symbol_lists_its_own_events_with_their_outcomes(tmp_path: Path) -> None:
    store, days = _with_dispositions(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start", symbol="1105")

    assert out.counts.candidates == 1
    assert out.horizons[0].status == "insufficient_sample" and out.horizons[0].n_events == 1
    assert len(out.events) == 1
    event = out.events[0]
    assert (event.symbol, event.event_date, event.day0_date) == (
        "1105",
        days[_EVENT_INDEX],
        days[_EVENT_INDEX],
    )
    by_horizon = {r.horizon_days: r for r in event.returns}
    assert by_horizon[5].return_pct == pytest.approx(5.0)
    assert by_horizon[60].not_measured == "censored" and by_horizon[60].return_pct is None
    assert event.name == "公司1105"


async def test_recent_events_are_newest_first_and_capped(tmp_path: Path) -> None:
    store, _days = _with_dispositions(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start", max_events=3)

    assert len(out.events) == 3
    assert [e.symbol for e in out.events] == ["1135", "1134", "1133"]  # same day: symbol desc


async def test_attention_listing_and_the_date_filter(tmp_path: Path) -> None:
    store, days = _build_store(tmp_path)
    flag = days[_EVENT_INDEX]
    rows = [
        {
            "symbol": s,
            "market": "TW",
            "company_name": f"公司{s}",
            "flagged_date": flag,
            "reason": "累積漲幅",
            "close": 100.0,
            "pe": None,
            "security_type": "tpex",
        }
        for s in _symbols()
    ]
    store.write_partition(ATTENTION_STOCKS_DATASET, "TW", flag, rows)
    tool = _tool(store)

    everything = await _run(tool, event_type="attention_listed")
    later_only = await _run(
        tool, event_type="attention_listed", start_date=flag + timedelta(days=1)
    )

    assert everything.counts.candidates == _N_STOCKS
    assert later_only.data_available is False
    assert "No matching events" in (later_only.unavailable_reason or "")


async def test_horizons_are_configurable_and_validated(tmp_path: Path) -> None:
    store, _days = _with_dispositions(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start", horizons=[10, 5, 10])

    assert [h.horizon_days for h in out.horizons] == [5, 10]
    for bad in ([], [0], [121], [1, 2, 3, 4, 5, 6]):
        with pytest.raises(ValidationError):
            GetEventStudyInput.model_validate({"event_type": "attention_listed", "horizons": bad})


async def test_no_stored_event_feed_is_reported_not_invented(tmp_path: Path) -> None:
    store, _days = _build_store(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start")

    assert out.data_available is False
    assert out.horizons == () and out.chart_hint is None
    assert out.health_status is HealthStatus.UNAVAILABLE
    assert "disposition_events" in (out.unavailable_reason or "")


async def test_an_empty_store_has_no_price_history(tmp_path: Path) -> None:
    out = await _run(_tool(ParquetStore(root=tmp_path)), event_type="attention_listed")

    assert out.data_available is False
    assert "price history" in (out.unavailable_reason or "")


async def test_only_the_taiwan_market_is_supported(tmp_path: Path) -> None:
    store, _days = _with_dispositions(tmp_path)

    out = await _run(_tool(store), event_type="disposition_start", market="US")

    assert out.data_available is False


def _capital_reduction_result(symbol: str, days: list[date]) -> SourceResult:
    rows = [
        {
            "symbol": symbol,
            "market": "TW",
            "trade_date": day.isoformat(),
            "closing_price_on_last_trading_day": 10.0,
            "post_reduction_reference_price": 20.0,
            "limit_up": 22.0,
            "limit_down": 18.0,
            "opening_reference_price": 20.0,
            "exright_reference_price": 20.0,
            "reason_for_capital_reduction": "彌補虧損",
        }
        for day in days
    ]
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.EVENT_DRIVEN,
        fetched_at=datetime.now(UTC),
        currentness_caveat="stub capital reduction rows",
        payload={"rows": rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_capital_reduction_needs_one_symbol_and_never_scans_the_market(
    tmp_path: Path,
) -> None:
    store, _days = _build_store(tmp_path)
    router, adapter = make_router(Symbol(id="1101", market=Market.TW), {})

    out = await _run(GetEventStudyTool(store, router), event_type="capital_reduction_resume")

    assert out.data_available is False
    assert "one symbol" in (out.unavailable_reason or "")
    assert adapter.calls == []


async def test_capital_reduction_for_one_symbol_lists_its_own_events(tmp_path: Path) -> None:
    store, days = _build_store(tmp_path)
    symbol = Symbol(id="1105", market=Market.TW)
    router, adapter = make_router(
        symbol,
        {
            Capability.CAPITAL_REDUCTION_REFERENCE_PRICE: _capital_reduction_result(
                "1105", [days[_EVENT_INDEX], days[5]]
            )
        },
    )
    tool = GetEventStudyTool(store, router)

    out = await _run(tool, event_type="capital_reduction_resume", symbol="1105")

    assert out.data_available is True
    assert adapter.calls and adapter.calls[0][1].id == "1105"
    assert adapter.calls[0][2]["start_date"] == days[0].isoformat()
    assert out.counts.candidates == 2
    assert all(h.status == "insufficient_sample" for h in out.horizons)  # two events cannot fill 30
    assert out.pre_event is None  # the share basis changes at a capital reduction
    assert all(e.pre_event_return_pct is None for e in out.events)
    codes = {i.code for i in out.limitations}
    assert {"per_symbol_only", "price_basis_break"} <= codes
    assert "short_event_history" not in codes
    newest = out.events[0]
    assert newest.event_date == days[_EVENT_INDEX]
    assert {r.horizon_days: r.return_pct for r in newest.returns}[5] == pytest.approx(5.0)


def test_health_is_ok_for_a_fresh_window_and_stale_for_an_old_one() -> None:
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()

    assert _health(today) is HealthStatus.OK
    assert _health(today - timedelta(days=30)) is HealthStatus.STALE
    assert _health(None) is HealthStatus.UNAVAILABLE


def test_the_tool_description_tells_the_model_to_use_history_not_signals() -> None:
    description = GetEventStudyTool.description

    assert "descriptive historical statistics" in description
    assert "state n" in description
    assert "never present them as a signal" in description


def test_the_route_documents_its_tool_and_the_card_type_is_set() -> None:
    assert GetEventStudyTool.name == "get_event_study"
    assert GetEventStudyTool.card_type == "event_study"
