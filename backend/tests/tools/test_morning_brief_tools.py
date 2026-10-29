"""`get_morning_brief` composes other tools: sections degrade independently, unusual conditions are
flagged by rule, and nothing is estimated."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from governance.textscan import check_output
from schemas.enums import DataClass, Tier
from tools.base import ToolContext
from tools.morning_brief_tools import GetMorningBriefTool, MorningBriefInput, _night_futures


class _Any(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Stub:
    """A tool double: returns a fixed payload, raises, or is sponsor-gated."""

    def __init__(
        self,
        name: str,
        payload: dict[str, object] | None = None,
        *,
        boom: bool = False,
        sponsor: bool = False,
    ) -> None:
        self.name = name
        self.input_model = _Any
        self.output_model = _Any
        self.description = ""
        self.data_class = DataClass.SPONSOR if sponsor else DataClass.PUBLIC
        self.supports_row_preview = False
        self._payload = payload or {}
        self._boom = boom

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        del args, ctx
        if self._boom:
            raise RuntimeError("upstream down")
        return _Any(**self._payload)


class _Registry:
    def __init__(self, *tools: _Stub) -> None:
        self._tools = {t.name: t for t in tools}

    def get_tool(self, name: str) -> _Stub | None:
        return self._tools.get(name)


def _strip(vix_change: float = -3.0) -> _Stub:
    return _Stub(
        "get_us_market_index_strip",
        {
            "rows": [
                {
                    "key": "spx",
                    "label": "S&P 500",
                    "value": 7700.0,
                    "change_pct": 0.7,
                    "as_of": "d",
                },
                {
                    "key": "vix",
                    "label": "VIX",
                    "value": 20.0,
                    "change_pct": vix_change,
                    "as_of": "d",
                },
            ]
        },
    )


async def _run(registry: _Registry, **kwargs: tuple[str, ...]) -> dict[str, object]:
    tool = GetMorningBriefTool(registry)  # type: ignore[arg-type]
    out = await tool.run(MorningBriefInput(**kwargs), ToolContext(tier=Tier.PRO))
    return out.model_dump(mode="json")


async def test_every_source_failing_degrades_to_a_list_of_gaps_not_an_exception() -> None:
    result = await _run(_Registry())  # no tools registered at all
    assert result["us_overnight"] == []
    assert result["flags"] == []
    unavailable = " ".join(str(x) for x in result["unavailable"])  # type: ignore[attr-defined]
    assert "美股夜盤與指數" in unavailable
    assert "三大法人" in unavailable


async def test_a_raising_tool_and_a_sponsor_gated_tool_are_reported_separately() -> None:
    registry = _Registry(
        _Stub("get_us_market_index_strip", boom=True),
        _Stub("get_institutional_flow_total_market", sponsor=True),
    )
    tool = GetMorningBriefTool(registry)  # type: ignore[arg-type]
    out = await tool.run(MorningBriefInput(), ToolContext(tier=Tier.FREE))
    gaps = list(out.unavailable)  # type: ignore[attr-defined]
    assert any("美股夜盤與指數（暫時無法取得）" in g for g in gaps)
    assert any("三大法人（需升級方案）" in g for g in gaps)


async def test_a_vix_jump_is_flagged_by_rule() -> None:
    quiet = await _run(_Registry(_strip(vix_change=-3.0)))
    jump = await _run(_Registry(_strip(vix_change=16.0)))
    assert quiet["flags"] == []
    assert jump["flags"] == ["VIX 單日上升 16.0%"]


async def test_institution_net_is_summed_per_group_in_yi_for_the_latest_day() -> None:
    flows = _Stub(
        "get_institutional_flow_total_market",
        {
            "rows": [
                {"trade_date": "2026-10-01", "name": "Foreign_Investor", "buy": 1e9, "sell": 9e9},
                {"trade_date": "2026-10-02", "name": "Foreign_Investor", "buy": 5e9, "sell": 2e9},
                {
                    "trade_date": "2026-10-02",
                    "name": "Foreign_Dealer_Self",
                    "buy": 2e8,
                    "sell": 1e8,
                },
                {"trade_date": "2026-10-02", "name": "Investment_Trust", "buy": 1e9, "sell": 4e9},
            ]
        },
    )
    rows = {r["label"]: r for r in (await _run(_Registry(flows)))["institutions"]}  # type: ignore[attr-defined]
    assert rows["外資"]["text"] == "+31.0 億元"
    assert rows["投信"]["text"] == "-30.0 億元"
    assert rows["外資"]["note"] == "2026-10-02 買賣超"


async def test_etf_premium_above_one_percent_is_flagged_and_us_etf_premium_is_not_invented() -> (
    None
):
    history = _Stub(
        "get_price_history",
        {
            "rows": [
                {"symbol": "00403A", "trade_date": "2026-10-01", "close": 10.0},
                {"symbol": "00403A", "trade_date": "2026-10-02", "close": 10.2},
            ]
        },
    )
    premium = _Stub(
        "get_etf_premium_discount",
        {"rows": [{"as_of": "2026-10-02", "premium_discount_pct": 1.4}]},
    )
    result = await _run(_Registry(history, premium), etfs=("00403A", "QQQ"))
    etfs = {e["symbol"]: e for e in result["etfs"]}  # type: ignore[attr-defined]
    assert etfs["00403A"]["premium_pct"] == 1.4
    assert etfs["00403A"]["change_pct"] == 2.0
    assert etfs["QQQ"]["premium_pct"] is None
    assert etfs["QQQ"]["close"] == "N/A"
    assert any("00403A 折溢價 +1.40%" in f for f in result["flags"])  # type: ignore[attr-defined]


async def test_headlines_with_target_prices_or_calls_never_enter_the_brief() -> None:
    news = _Stub(
        "get_news_feed",
        {
            "items": [
                {"title": "大和證券力挺南電目標價 2,460 元", "source": "x", "published_at": "t"},
                {"title": "台積電法說會日期公布", "source": "y", "published_at": "t"},
            ]
        },
    )
    result = await _run(_Registry(news))
    titles = [h["title"] for h in result["headlines"]]  # type: ignore[attr-defined]
    assert titles == ["台積電法說會日期公布"]


async def test_calendar_events_carry_taipei_and_et_times() -> None:
    today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    calendar = _Stub(
        "get_macro_calendar",
        {
            "events": [
                {
                    "date": today,
                    "time_utc": f"{today}T14:00:00+00:00",
                    "title": "US ISM Services PMI",
                    "impact": "medium",
                    "consensus": None,
                    "prior": "54.1",
                }
            ]
        },
    )
    event = (await _run(_Registry(calendar)))["events_today"][0]  # type: ignore[index]
    assert event["taipei_time"].endswith("22:00")
    assert event["et_time"].endswith("10:00")
    assert event["consensus"] == "N/A"
    assert event["prior"] == "54.1"


class _Router:
    def __init__(self, payload: dict[str, object] | None, *, boom: bool = False) -> None:
        self._payload = payload
        self._boom = boom
        self.asked: list[tuple[str, str]] = []

    async def fetch(self, cap: object, sym: object, **kwargs: object) -> object:
        del kwargs
        self.asked.append((str(getattr(cap, "value", cap)), f"{sym.id}@{sym.market.value}"))  # type: ignore[attr-defined]
        if self._boom:
            raise RuntimeError("down")
        return type("R", (), {"payload": self._payload or {}})()


async def test_taiex_comes_from_the_quote_bars_with_the_change_against_the_prior_close() -> None:
    router = _Router(
        {
            "bars": [
                {"trade_date": "2026-10-01", "close": 48000.0},
                {"trade_date": "2026-10-02", "close": 48480.0},
            ]
        }
    )
    tool = GetMorningBriefTool(_Registry(), router)  # type: ignore[arg-type]
    out = await tool.run(MorningBriefInput(), ToolContext(tier=Tier.PRO))
    taiex = next(r for r in out.tw_market if "TAIEX" in r.label)  # type: ignore[attr-defined]
    assert taiex.text == "48,480.00"
    assert taiex.change_pct == 1.0
    assert ("price_eod", "^TWII@US") in router.asked


async def test_taiex_is_na_and_listed_as_a_gap_when_the_quote_service_fails() -> None:
    tool = GetMorningBriefTool(_Registry(), _Router(None, boom=True))  # type: ignore[arg-type]
    out = await tool.run(MorningBriefInput(), ToolContext(tier=Tier.PRO))
    taiex = next(r for r in out.tw_market if "TAIEX" in r.label)  # type: ignore[attr-defined]
    assert taiex.text == "N/A"
    assert any("加權指數" in g for g in out.unavailable)  # type: ignore[attr-defined]


def _strip_item(key: str, label: str, value: float, pct: float, as_of: str) -> dict[str, object]:
    return {"key": key, "label": label, "value": value, "change_pct": pct, "as_of": as_of}


async def test_fx_gold_oil_and_otc_come_from_the_taiwan_ticker_strip_with_their_own_dates() -> None:
    strip = _Stub(
        "get_market_index_strip",
        {
            "rows": [
                {
                    "key": "otc",
                    "label": "櫃買指數",
                    "value": 794.0,
                    "change_pct": 1.9,
                    "as_of": "2026-10-02",
                },
                {
                    "key": "usd_twd",
                    "label": "美元/台幣",
                    "value": 31.795,
                    "change_pct": -0.03,
                    "as_of": "2026-10-02",
                },
                {
                    "key": "crude_oil_wti",
                    "label": "原油 WTI",
                    "value": 96.16,
                    "change_pct": -3.2,
                    "as_of": "2026-09-29",
                },
            ]
        },
    )
    result = await _run(_Registry(strip))
    macro = {r["label"]: r for r in result["macro"]}  # type: ignore[attr-defined]
    assert macro["美元/台幣"]["text"] == "31.80"
    assert macro["原油 WTI"]["note"] == "2026-09-29"  # the lag is visible, not hidden
    assert any(r["label"] == "櫃買指數" for r in result["tw_market"])  # type: ignore[attr-defined]


async def test_general_tech_and_security_headlines_are_not_market_news() -> None:
    news = _Stub(
        "get_news_feed",
        {
            "items": [
                {"title": "Dell 修補容器儲存套件漏洞", "source": "a", "published_at": "t"},
                {"title": "外資連三日買超台積電", "source": "b", "published_at": "t"},
            ]
        },
    )
    titles = [h["title"] for h in (await _run(_Registry(news)))["headlines"]]  # type: ignore[attr-defined]
    assert titles == ["外資連三日買超台積電"]


async def test_a_news_or_calendar_outage_is_a_data_gap_not_a_quiet_empty_list() -> None:
    down = {"health_status": "unreachable", "items": [], "events": []}
    result = await _run(_Registry(_Stub("get_news_feed", down), _Stub("get_macro_calendar", down)))
    gaps = " ".join(str(x) for x in result["unavailable"])  # type: ignore[attr-defined]
    assert "新聞標題（來源暫時無法取得）" in gaps
    assert "今日行事曆（來源暫時無法取得）" in gaps


async def test_an_answering_news_source_with_no_items_is_not_reported_as_down() -> None:
    quiet = {"health_status": "stale", "items": []}
    result = await _run(_Registry(_Stub("get_news_feed", quiet)))
    gaps = " ".join(str(x) for x in result["unavailable"])  # type: ignore[attr-defined]
    assert "新聞標題（來源暫時無法取得）" not in gaps


def _night(day: str, contract: str, close: float, pct: float, volume: int) -> dict[str, object]:
    return {
        "trade_date": day,
        "contract_date": contract,
        "trading_session": "after_market",
        "close": close,
        "spread_per": pct,
        "volume": volume,
    }


def _day(day: str, contract: str, close: float, pct: float, volume: int) -> dict[str, object]:
    return {**_night(day, contract, close, pct, volume), "trading_session": "position"}


_TODAY = date(2026, 10, 29)


def test_night_row_is_the_busiest_single_month_contract_of_the_latest_night_session() -> None:
    data: dict[str, object] = {
        "rows": [
            _night("2026-10-27", "202611", 41000.0, 0.1, 9000),
            _night("2026-10-28", "202611", 41500.0, 0.8, 52000),
            _night("2026-10-28", "202612", 41400.0, 0.7, 3000),
            _night("2026-10-28", "202611/202612", 100.0, 9.9, 99999),
            _day("2026-10-28", "202611", 41300.0, 0.3, 120000),
        ]
    }
    row = _night_futures(data, _TODAY)
    assert row is not None
    assert row.label == "台指期夜盤（近月）"
    assert row.text == "41,500"
    assert row.change_pct == 0.8
    assert row.note == "2026-10-28 較前一日盤結算"


def test_night_row_takes_the_last_available_day_when_calendar_days_are_missing() -> None:
    data: dict[str, object] = {"rows": [_night("2026-10-23", "202611", 40800.0, -0.4, 5)]}
    row = _night_futures(data, _TODAY)
    assert row is not None
    assert row.note.startswith("2026-10-23")


def test_night_row_is_absent_without_night_rows() -> None:
    only_day: dict[str, object] = {"rows": [_day("2026-10-28", "202611", 41300.0, 0.3, 1)]}
    only_spread: dict[str, object] = {"rows": [_night("2026-10-28", "202611/202612", 5.0, 0.0, 1)]}
    assert _night_futures(None, _TODAY) is None
    assert _night_futures({"rows": []}, _TODAY) is None
    assert _night_futures(only_day, _TODAY) is None
    assert _night_futures(only_spread, _TODAY) is None


def test_night_row_ignores_sessions_dated_after_today() -> None:
    data: dict[str, object] = {"rows": [_night("2026-10-30", "202611", 41500.0, 0.8, 1)]}
    assert _night_futures(data, _TODAY) is None


class _TwRouter:
    def __init__(self, futures: dict[str, object], *, futures_boom: bool = False) -> None:
        self._futures = futures
        self._futures_boom = futures_boom

    async def fetch(self, cap: object, sym: object, **kwargs: object) -> object:
        del sym, kwargs
        if str(getattr(cap, "value", cap)) == "futures_daily":
            if self._futures_boom:
                raise RuntimeError("down")
            return type("R", (), {"payload": self._futures})()
        return type("R", (), {"payload": {}})()


async def test_night_futures_row_lands_in_the_tw_table_and_passes_the_output_guardrails() -> None:
    day = (datetime.now(ZoneInfo("Asia/Taipei")).date() - timedelta(days=1)).isoformat()
    router = _TwRouter({"rows": [_night(day, "202611", 41500.0, 0.8, 52000)]})
    tool = GetMorningBriefTool(_Registry(), router)  # type: ignore[arg-type]
    out = await tool.run(MorningBriefInput(), ToolContext(tier=Tier.PRO))
    night = next(r for r in out.tw_market if r.label == "台指期夜盤（近月）")  # type: ignore[attr-defined]
    assert night.text == "41,500"
    assert not any("台指期夜盤" in g for g in out.unavailable)  # type: ignore[attr-defined]
    report: str = out.report  # type: ignore[attr-defined]
    assert "| 台指期夜盤（近月） | 41,500 | +0.8% |" in report
    verdict = check_output(report)
    assert verdict.allowed
    assert verdict.reason is None
    assert verdict.redacted_text in (None, report)


async def test_a_failing_futures_read_is_listed_once_and_the_rest_of_the_brief_survives() -> None:
    router = _TwRouter({}, futures_boom=True)
    tool = GetMorningBriefTool(_Registry(_strip()), router)  # type: ignore[arg-type]
    out = await tool.run(MorningBriefInput(), ToolContext(tier=Tier.PRO))
    gaps = [g for g in out.unavailable if "台指期夜盤" in g]  # type: ignore[attr-defined]
    assert gaps == ["台指期夜盤（近月）（暫時無法取得）"]
    assert not any(r.label == "台指期夜盤（近月）" for r in out.tw_market)  # type: ignore[attr-defined]
    assert len(out.us_overnight) == 2  # type: ignore[arg-type]


async def test_a_futures_read_without_night_rows_is_listed_as_empty() -> None:
    router = _TwRouter({"rows": [_day("2026-10-28", "202611", 41300.0, 0.3, 1)]})
    tool = GetMorningBriefTool(_Registry(), router)  # type: ignore[arg-type]
    out = await tool.run(MorningBriefInput(), ToolContext(tier=Tier.PRO))
    assert "台指期夜盤（近月）（來源無資料）" in out.unavailable  # type: ignore[attr-defined]
