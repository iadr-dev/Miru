"""`get_morning_brief` — the data behind the 晨間摘要, assembled by code and handed to the model.

One call gathers what a pre-open briefing needs — overnight US close, macro, TAIEX and the TSMC
ADR, the three major institutions, breadth and sector moves, today's calendar, the user's own
watchlist stocks/ETFs, and headlines — by running the existing single-purpose tools concurrently.
Each section is independent: if a source is empty, slow or sponsor-gated, that section is reported
in `unavailable` and the rest still arrives, so the briefing says 「N/A」 instead of guessing.

The model only writes prose around these numbers (the same split the report skills use), and
`flags` carries the unusual-condition checks (a VIX jump, an ETF premium, a big index move) so they
are computed by rule, not noticed by the model. Nothing here ranks, scores or recommends.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, cast
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel

from governance.textscan import check_output
from schemas.chart_hint import ChartHint, FieldDef, FieldType, Mark
from schemas.enums import Capability, DataClass, Market
from schemas.market import Symbol
from tools.base import BaseTool, MaskedTeaser, ToolContext, dispatch_tool, parse_args
from tools.morning_brief_models import (
    BarPoint,
    BriefEtf,
    BriefEvent,
    BriefHeadline,
    BriefRow,
    GetMorningBriefOutput,
    LinePoint,
    MorningBriefInput,
)
from tools.morning_brief_render import render_morning_report

if TYPE_CHECKING:
    from dataaccess.router import DataAccessRouter
    from tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)

_TAIPEI = ZoneInfo("Asia/Taipei")
_NEW_YORK = ZoneInfo("America/New_York")
_MAX_EACH = 5
_CALL_TIMEOUT_SECONDS = 25.0
_LOOKBACK_DAYS = 7
_NA = "N/A"
_YI = 1e8  # NT$ 億
_SECTOR_COUNT = 3
_HEADLINE_COUNT = 6
_EVENT_COUNT = 8

# Unusual-condition thresholds, in percent. Flags are observations about the data, not signals.
_VIX_JUMP_PCT = 15.0
_INDEX_MOVE_PCT = 2.0
_SOX_MOVE_PCT = 3.0
_ADR_MOVE_PCT = 3.0
_ETF_PREMIUM_PCT = 1.0

_INSTITUTION_GROUPS: dict[str, tuple[str, ...]] = {
    "外資": ("Foreign_Investor", "Foreign_Dealer_Self"),
    "投信": ("Investment_Trust",),
    "自營商": ("Dealer_self", "Dealer_Hedging"),
}

Row = dict[str, object]


# ---- defensive accessors over the tools' JSON-ready output ---------------------------------------


def _rows(data: Row | None, key: str) -> list[Row]:
    value = data.get(key) if data else None
    if not isinstance(value, list):
        return []
    items = cast("list[object]", value)
    return [cast("Row", item) for item in items if isinstance(item, dict)]


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _fmt(value: float | None, digits: int = 2) -> str:
    return f"{value:,.{digits}f}" if value is not None else _NA


def _signed(value: float | None, suffix: str = "%") -> str:
    return f"{value:+.2f}{suffix}" if value is not None else _NA


def _pct(now: float | None, before: float | None) -> float | None:
    if now is None or before is None or before == 0:
        return None
    return round((now - before) / before * 100, 2)


def _last_two(rows: list[Row], date_key: str, value_key: str) -> tuple[float | None, float | None]:
    """(latest, previous) values by distinct date — duplicates on a date keep the last reading."""
    by_date: dict[str, float] = {}
    for r in sorted(rows, key=lambda r: _str(r.get(date_key))):
        value = _num(r.get(value_key))
        if value is not None:
            by_date[_str(r.get(date_key))] = value
    values = list(by_date.values())
    if not values:
        return None, None
    return values[-1], values[-2] if len(values) > 1 else None


def _level(label: str, latest: float | None, before: float | None, note: str = "") -> BriefRow:
    return BriefRow(label=label, text=_fmt(latest), change_pct=_pct(latest, before), note=note)


# ---- sections --------------------------------------------------------------------------------


def _dated_note(as_of: str, today: date) -> str:
    return as_of if as_of and not as_of.startswith(today.isoformat()) else ""


def _us_overnight(data: Row | None, today: date) -> list[BriefRow]:
    return [
        BriefRow(
            label=_str(r.get("label")) or _str(r.get("key")),
            text=_fmt(_num(r.get("value"))),
            change_pct=_num(r.get("change_pct")),
            # Only a quote dated before today is worth a date (e.g. the policy rate's last change).
            note=_dated_note(_str(r.get("as_of")), today),
        )
        for r in _rows(data, "rows")
    ]


_STRIP_MACRO_KEYS = ("usd_twd", "jpy_twd", "gold", "crude_oil_wti", "crude_oil_brent")


def _strip_row(r: Row) -> BriefRow:
    digits = 4 if _str(r.get("key")) == "jpy_twd" else 2
    return BriefRow(
        label=_str(r.get("label")),
        text=_fmt(_num(r.get("value")), digits),
        change_pct=_num(r.get("change_pct")),
        note=_str(r.get("as_of")),
    )


def _macro(data: Row | None) -> list[BriefRow]:
    """匯率、黃金與原油 from the Taiwan ticker strip; each carries its own data date (oil spot
    prices in particular can trail by days)."""
    by_key = {_str(r.get("key")): r for r in _rows(data, "rows")}
    return [_strip_row(by_key[k]) for k in _STRIP_MACRO_KEYS if k in by_key]


def _otc(data: Row | None) -> BriefRow | None:
    row = next((r for r in _rows(data, "rows") if _str(r.get("key")) == "otc"), None)
    return _strip_row(row) if row else None


def _taiex(data: Row | None) -> BriefRow:
    latest, before = _last_two(_rows(data, "bars"), "trade_date", "close")
    return _level("加權指數 TAIEX", latest, before, "前一交易日收盤" if latest else "")


def _adr(data: Row | None) -> BriefRow:
    latest, before = _last_two(_rows(data, "price"), "trade_date", "close")
    return _level("台積電 ADR（TSM）", latest, before, "美股前一交易日收盤" if latest else "")


def _institutions(data: Row | None) -> list[BriefRow]:
    rows = _rows(data, "rows")
    if not rows:
        return []
    latest = max(_str(r.get("trade_date")) for r in rows)
    net: dict[str, float] = defaultdict(float)
    for r in rows:
        if _str(r.get("trade_date")) != latest:
            continue
        delta = (_num(r.get("buy")) or 0.0) - (_num(r.get("sell")) or 0.0)
        for group, members in _INSTITUTION_GROUPS.items():
            if _str(r.get("name")) in members:
                net[group] += delta
    return [
        BriefRow(label=group, text=f"{value / _YI:+,.1f} 億元", note=f"{latest} 買賣超")
        for group, value in net.items()
    ]


def _sector_row(r: Row, day: str) -> BriefRow:
    avg = _num(r.get("avg_pct_change"))
    up = int(_num(r.get("advancing_count")) or 0)
    down = int(_num(r.get("declining_count")) or 0)
    return BriefRow(
        label=_str(r.get("industry")),
        text=_signed(avg),
        change_pct=avg,
        note=f"上漲 {up} 家、下跌 {down} 家（{day}）",
    )


def _sectors(data: Row | None) -> tuple[list[BriefRow], list[BriefRow]]:
    ranked = sorted(
        (r for r in _rows(data, "industries") if _num(r.get("avg_pct_change")) is not None),
        key=lambda r: _num(r.get("avg_pct_change")) or 0.0,
        reverse=True,
    )
    day = _str((data or {}).get("trade_date"))
    top = [_sector_row(r, day) for r in ranked[:_SECTOR_COUNT]]
    bottom = [_sector_row(r, day) for r in reversed(ranked[-_SECTOR_COUNT:])]
    return top, bottom


def _clock(moment: str) -> tuple[str, str]:
    """`time_utc` as (Taipei, ET) clock text; the raw text if it cannot be parsed."""
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return moment or _NA, _NA
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    fmt = "%m/%d %H:%M"
    return parsed.astimezone(_TAIPEI).strftime(fmt), parsed.astimezone(_NEW_YORK).strftime(fmt)


def _events(data: Row | None, today: date) -> list[BriefEvent]:
    first, last = today.isoformat(), (today + timedelta(days=1)).isoformat()
    out: list[BriefEvent] = []
    for r in _rows(data, "events"):
        day = _str(r.get("date"))
        if day and not first <= day <= last:
            continue
        taipei, et = _clock(_str(r.get("time_utc")))
        out.append(
            BriefEvent(
                name=_str(r.get("title")) or _str(r.get("name")),
                taipei_time=taipei,
                et_time=et,
                impact=_str(r.get("impact")),
                consensus=_str(r.get("consensus")) or _NA,
                prior=_str(r.get("prior")) or _NA,
            )
        )
    return sorted(out, key=lambda e: e.taipei_time)[:_EVENT_COUNT]


def _closes(data: Row | None) -> dict[str, tuple[str, float | None]]:
    """Per symbol: (latest close text, % change vs the prior session) from a price-history read."""
    by_symbol: dict[str, list[Row]] = defaultdict(list)
    for r in _rows(data, "rows"):
        by_symbol[_str(r.get("symbol"))].append(r)
    out: dict[str, tuple[str, float | None]] = {}
    for symbol, rows in by_symbol.items():
        latest, before = _last_two(rows, "trade_date", "close")
        if latest is not None:
            out[symbol] = (_fmt(latest), _pct(latest, before))
    return out


def _headlines(data: Row | None) -> tuple[BriefHeadline, ...]:
    # A third-party headline can carry price targets or buy/sell calls; those never enter the
    # briefing (the same scan every output passes).
    return tuple(
        BriefHeadline(
            title=_str(r.get("title")),
            source=_str(r.get("source")),
            published_at=_str(r.get("published_at")),
        )
        for r in _rows(data, "items")
        if _str(r.get("title")) and _clean(_str(r.get("title"))) and _on_topic(_str(r.get("title")))
    )[:_HEADLINE_COUNT]


_NAME = FieldDef(field="name", type=FieldType.NOMINAL)
_VALUE = FieldDef(field="value", type=FieldType.QUANTITATIVE)
_DATE = FieldDef(field="date", type=FieldType.TEMPORAL)
_SYMBOL = FieldDef(field="symbol", type=FieldType.NOMINAL)
_INDEX_LABELS = ("S&P", "DOW", "NASDAQ", "SOX", "半導體", "VIX")


def _bars(rows: list[BriefRow]) -> tuple[BarPoint, ...]:
    return tuple(
        BarPoint(name=r.label, value=round(r.change_pct, 2))
        for r in rows
        if r.change_pct is not None
    )


def _us_change_series(rows: list[BriefRow]) -> tuple[BarPoint, ...]:
    """Equity indices and VIX only — a yield or a policy rate is a level, not a daily move."""
    return _bars([r for r in rows if any(k in r.label.upper() for k in _INDEX_LABELS)])


def _flow_series(rows: list[BriefRow]) -> tuple[BarPoint, ...]:
    points: list[BarPoint] = []
    for r in rows:
        try:
            points.append(
                BarPoint(name=r.label, value=float(r.text.replace("億元", "").replace(",", "")))
            )
        except ValueError:
            continue
    return tuple(points)


def _indexed_lines(rows_by_market: list[Row | None]) -> tuple[LinePoint, ...]:
    """Each symbol's recent closes rebased to 100 at the first close in the window."""
    series: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for data in rows_by_market:
        for r in sorted(_rows(data, "rows"), key=lambda r: _str(r.get("trade_date"))):
            close = _num(r.get("close"))
            if close is not None:
                series[_str(r.get("symbol"))].append((_str(r.get("trade_date")), close))
    points: list[LinePoint] = []
    for symbol, values in series.items():
        if len(values) < 2 or values[0][1] == 0:  # noqa: PLR2004 — a line needs two points
            continue
        base = values[0][1]
        points.extend(
            LinePoint(date=day, symbol=symbol, value=round(close / base * 100, 2))
            for day, close in values
        )
    return tuple(points)


def _chart_hints(out: GetMorningBriefOutput) -> tuple[ChartHint, ...]:
    hints: list[ChartHint] = []
    if out.us_change_series:
        hints.append(
            ChartHint(
                mark=Mark.BAR,
                title="美股指數前一交易日漲跌幅（%）",
                data_key="us_change_series",
                x=_NAME,
                y=_VALUE,
                diverging=True,
            )
        )
    if out.flow_series:
        hints.append(
            ChartHint(
                mark=Mark.BAR,
                title="三大法人買賣超（億元）",
                data_key="flow_series",
                x=_NAME,
                y=_VALUE,
                diverging=True,
            )
        )
    if out.sector_series:
        hints.append(
            ChartHint(
                mark=Mark.BAR,
                title="類股漲跌幅（%，上漲前三與下跌前三）",
                data_key="sector_series",
                x=_NAME,
                y=_VALUE,
                diverging=True,
            )
        )
    if out.watch_series:
        hints.append(
            ChartHint(
                mark=Mark.LINE,
                title="自選標的近五個交易日走勢（起點 = 100）",
                data_key="watch_series",
                x=_DATE,
                y=_VALUE,
                color=_SYMBOL,
            )
        )
    return tuple(hints)


# A headline is kept only if it is about markets or the economy; the feeds also carry general tech
# and security news that has no place in a market briefing.
_MARKET_WORDS = [
    "股",
    "指數",
    "台積電",
    "ETF",
    "央行",
    "Fed",
    "聯準會",
    "利率",
    "通膨",
    "升息",
    "降息",
    "殖利率",
    "匯率",
    "美元",
    "台幣",
    "油價",
    "黃金",
    "財報",
    "營收",
    "法說",
    "除息",
    "配息",
    "外資",
    "投信",
    "半導體",
    "晶圓",
    "輝達",
    "Nvidia",
    "NVIDIA",
    "S&P",
    "Nasdaq",
    "道瓊",
    "關稅",
    "GDP",
    "CPI",
    "PMI",
    "經濟",
    "景氣",
    "出口",
    "訂單",
    "市值",
    "IPO",
    "併購",
]


def _on_topic(title: str) -> bool:
    return any(word in title for word in _MARKET_WORDS)


def _clean(text: str) -> bool:
    return check_output(text, has_market_data=False).reason != "trading_language_redacted"


def _us_movers(data: Row | None) -> list[BriefRow]:
    return [
        BriefRow(
            label=f"{_str(r.get('symbol'))} {_str(r.get('name'))}".strip(),
            text=_fmt(_num(r.get("price"))),
            change_pct=_num(r.get("change_pct")),
        )
        for r in _rows(data, "rows")[:_MAX_EACH]
    ]


def _tw_movers(data: Row | None) -> list[BriefRow]:
    out: list[BriefRow] = []
    for key, tag in (("top_gainers", "漲幅居前"), ("top_losers", "跌幅居前")):
        for r in _rows(data, key)[:_SECTOR_COUNT]:
            change = _num(r.get("pct_change"))
            label = f"{_str(r.get('symbol'))} {_str(r.get('stock_name'))}".strip()
            out.append(BriefRow(label=label, text=_signed(change), change_pct=change, note=tag))
    return out


def _etf_rows(
    tw: list[str],
    us: list[str],
    tw_hist: Row | None,
    us_hist: Row | None,
    premiums: dict[str, Row | None],
) -> list[BriefEtf]:
    out: list[BriefEtf] = []
    tw_px, us_px = _closes(tw_hist), _closes(us_hist)
    for symbol in tw:
        text, change = tw_px.get(symbol, (_NA, None))
        latest = max(
            _rows(premiums.get(symbol), "rows"),
            key=lambda r: _str(r.get("as_of")),
            default=None,
        )
        premium = _num(latest.get("premium_discount_pct")) if latest else None
        note = "折溢價為發行人預估淨值的估算" if premium is not None else "折溢價資料未提供"
        out.append(
            BriefEtf(
                symbol=symbol,
                market=Market.TW,
                close=text,
                change_pct=change,
                premium_pct=premium,
                note=note,
            )
        )
    for symbol in us:
        text, change = us_px.get(symbol, (_NA, None))
        out.append(
            BriefEtf(
                symbol=symbol,
                market=Market.US,
                close=text,
                change_pct=change,
                note="美股 ETF 折溢價本系統未提供",
            )
        )
    return out


def _flags(us: list[BriefRow], tw: list[BriefRow], etfs: list[BriefEtf]) -> list[str]:
    flags: list[str] = []
    for r in us:
        chg, name = r.change_pct, r.label.upper()
        if chg is None:
            continue
        if name.startswith("VIX") and chg >= _VIX_JUMP_PCT:
            flags.append(f"VIX 單日上升 {chg:.1f}%")
        elif "SOX" in name and abs(chg) >= _SOX_MOVE_PCT:
            flags.append(f"費城半導體指數單日 {chg:+.1f}%")
        elif name.startswith(("S&P", "NASDAQ", "DOW")) and abs(chg) >= _INDEX_MOVE_PCT:
            flags.append(f"{r.label}單日 {chg:+.1f}%")
    for r in tw:
        if r.change_pct is None:
            continue
        if "TAIEX" in r.label and abs(r.change_pct) >= _INDEX_MOVE_PCT:
            flags.append(f"加權指數單日 {r.change_pct:+.1f}%")
        if "ADR" in r.label and abs(r.change_pct) >= _ADR_MOVE_PCT:
            flags.append(f"台積電 ADR 單日 {r.change_pct:+.1f}%")
    flags.extend(
        f"{e.symbol} 折溢價 {e.premium_pct:+.2f}%（絕對值超過 {_ETF_PREMIUM_PCT:.0f}%）"
        for e in etfs
        if e.premium_pct is not None and abs(e.premium_pct) > _ETF_PREMIUM_PCT
    )
    return flags


# ---- the tool ----------------------------------------------------------------------------------


class _Collector:
    """Runs one source at a time, isolating failures so one dead source never sinks the report."""

    def __init__(
        self, registry: ToolRegistry, ctx: ToolContext, router: DataAccessRouter | None
    ) -> None:
        self._registry = registry
        self._ctx = ctx
        self._router = router
        self.unavailable: list[str] = []

    async def bars(self, label: str, symbol: str, market: Market, span: Row) -> Row | None:
        """Daily bars for a symbol the ingest does not hold (the TAIEX index), via the router's
        price capability — the same path the price tools use."""
        if self._router is None:
            self.unavailable.append(f"{label}（暫時無法取得）")
            return None
        try:
            result = await asyncio.wait_for(
                self._router.fetch(
                    Capability.PRICE_EOD,
                    Symbol(id=symbol, market=market),
                    tls_policy=self._ctx.tls_policy,
                    **span,
                ),
                timeout=_CALL_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.warning("morning_brief_bars_failed", symbol=symbol)
            self.unavailable.append(f"{label}（暫時無法取得）")
            return None
        return result.payload

    async def call(self, label: str, tool_name: str, args: Row) -> Row | None:
        tool = self._registry.get_tool(tool_name)
        if tool is None:
            self.unavailable.append(f"{label}（工具未提供）")
            return None
        try:
            validated = tool.input_model.model_validate(args)
            result = await asyncio.wait_for(
                dispatch_tool(tool, validated, self._ctx), timeout=_CALL_TIMEOUT_SECONDS
            )
        except Exception:
            logger.warning("morning_brief_section_failed", tool=tool_name)
            self.unavailable.append(f"{label}（暫時無法取得）")
            return None
        if isinstance(result, MaskedTeaser):
            self.unavailable.append(f"{label}（需升級方案）")
            return None
        return result.model_dump(mode="json")

    async def maybe(self, cond: bool, label: str, tool_name: str, args: Row) -> Row | None:
        return await self.call(label, tool_name, args) if cond else None


class GetMorningBriefTool(BaseTool):
    """`get_morning_brief` — see the module docstring."""

    name = "get_morning_brief"
    description = (
        "Get the pre-computed data for the 晨間摘要 (morning briefing): US overnight close "
        "(S&P 500, Dow, Nasdaq, SOX, VIX, 10Y yield), macro (USD/TWD, gold, oil, Fear & Greed), "
        "TAIEX and the TSMC ADR, three-institution net flows, breadth and sector moves, today's "
        "macro calendar in Taipei and ET time, the user's own TW/US stocks and ETFs (close, % "
        "change, ETF premium), US and TW movers, headlines, and rule-based unusual-condition "
        "flags. Any missing section is listed in `unavailable` — write N/A, never estimate. The "
        "result includes `report`, the finished 晨間摘要 text; reproduce it verbatim."
    )
    input_model: type[BaseModel] = MorningBriefInput
    output_model: type[BaseModel] = GetMorningBriefOutput
    data_class = DataClass.PUBLIC
    card_type: str | None = "morning_brief"

    def __init__(self, registry: ToolRegistry, router: DataAccessRouter | None = None) -> None:
        self._registry = registry
        self._router = router

    async def run(self, args: BaseModel, ctx: ToolContext) -> BaseModel:
        typed = parse_args(MorningBriefInput, args)
        now = datetime.now(_TAIPEI)
        today = now.date()
        span: Row = {
            "start_date": (today - timedelta(days=_LOOKBACK_DAYS)).isoformat(),
            "end_date": today.isoformat(),
        }
        tw_etfs = [e for e in typed.etfs if e.startswith("00")]
        us_etfs = [e for e in typed.etfs if not e.startswith("00")]
        c = _Collector(self._registry, ctx, self._router)

        def history(symbols: list[str], market: str) -> Row:
            return {"symbols": symbols, "market": market, "days": 5}

        plan: dict[str, Awaitable[Row | None]] = {
            "us_strip": c.call("美股夜盤與指數", "get_us_market_index_strip", {}),
            "macro": c.call("匯率、黃金與原油", "get_market_index_strip", {}),
            "taiex": c.bars("加權指數", "^TWII", Market.US, span),
            "adr": c.call(
                "台積電 ADR", "get_international_stock", {"id": "TSM", "market": "US", **span}
            ),
            "flows": c.call(
                "三大法人", "get_institutional_flow_total_market", {"market": "TW", **span}
            ),
            "breadth": c.call("漲跌家數", "get_advance_decline", {"market": "TW"}),
            "sectors": c.call("類股漲跌", "get_sector_heatmap", {"market": "TW"}),
            "calendar": c.call("今日行事曆", "get_macro_calendar", {"days_ahead": 2}),
            "us_rank": c.call(
                "美股熱門", "get_us_market_rankings", {"kind": "trending", "top_n": 5}
            ),
            "tw_mom": c.call(
                "台股漲跌幅", "get_price_momentum", {"market": "TW", "period": "day", "top_n": 3}
            ),
            "news": c.call("新聞標題", "get_news_feed", {"query": "台股 美股", "market": "TW"}),
            "tw_hist": c.maybe(
                bool(typed.tw_stocks),
                "自選台股",
                "get_price_history",
                history(list(typed.tw_stocks), "TW"),
            ),
            "us_hist": c.maybe(
                bool(typed.us_stocks),
                "自選美股",
                "get_price_history",
                history(list(typed.us_stocks), "US"),
            ),
            "tw_etf_hist": c.maybe(
                bool(tw_etfs), "台股 ETF", "get_price_history", history(tw_etfs, "TW")
            ),
            "us_etf_hist": c.maybe(
                bool(us_etfs), "美股 ETF", "get_price_history", history(us_etfs, "US")
            ),
        }
        for etf in tw_etfs:
            plan[f"premium:{etf}"] = c.call(
                f"{etf} 折溢價", "get_etf_premium_discount", {"id": etf, "market": "TW", "days": 2}
            )
        got = dict(zip(plan, await asyncio.gather(*plan.values()), strict=True))
        us_strip, macro, taiex, adr = got["us_strip"], got["macro"], got["taiex"], got["adr"]
        flows, breadth, sectors, calendar = (
            got["flows"],
            got["breadth"],
            got["sectors"],
            got["calendar"],
        )
        tw_hist, us_hist = got["tw_hist"], got["us_hist"]
        tw_etf_hist, us_etf_hist = got["tw_etf_hist"], got["us_etf_hist"]
        us_rank, tw_mom, news = got["us_rank"], got["tw_mom"], got["news"]
        premiums = [got[f"premium:{e}"] for e in tw_etfs]

        us_rows = _us_overnight(us_strip, today)
        tw_rows = [_taiex(taiex), _adr(adr)]
        if (otc := _otc(macro)) is not None:
            tw_rows.append(otc)
        up, down = _sectors(sectors)
        inst = _institutions(flows)
        advancing = _num((breadth or {}).get("advancing_count"))
        declining = _num((breadth or {}).get("declining_count"))
        if advancing is not None and declining is not None:
            text = f"上漲 {int(advancing)} 家、下跌 {int(declining)} 家"
            tw_rows.append(
                BriefRow(label="漲跌家數", text=text, note=_str((breadth or {}).get("trade_date")))
            )

        watch = [
            BriefRow(label=f"{sym}（台股）", text=text, change_pct=chg, note="前一交易日收盤")
            for sym, (text, chg) in _closes(tw_hist).items()
        ] + [
            BriefRow(label=f"{sym}（美股）", text=text, change_pct=chg, note="美股前一交易日收盤")
            for sym, (text, chg) in _closes(us_hist).items()
        ]
        etfs = _etf_rows(
            tw_etfs, us_etfs, tw_etf_hist, us_etf_hist, dict(zip(tw_etfs, premiums, strict=False))
        )
        tw_day = max((r.note[:10] for r in inst if r.note), default="")
        sector_day = _str((sectors or {}).get("trade_date"))
        if tw_day and sector_day and sector_day < tw_day:
            c.unavailable.append(
                f"類股與漲跌家數的資料日為 {sector_day}，較最新交易日 {tw_day} 落後"
            )
        empty = {
            "匯率、黃金與原油": not _macro(macro),
            "加權指數": tw_rows[0].text == _NA,
            "三大法人": not inst,
            "類股漲跌": not up,
        }
        c.unavailable.extend(
            f"{name}（來源無資料）" for name, is_empty in empty.items() if is_empty
        )
        draft = GetMorningBriefOutput(
            date_taipei=today.isoformat(),
            time_taipei=now.strftime("%H:%M"),
            us_overnight=tuple(us_rows),
            macro=tuple(_macro(macro)),
            tw_market=tuple(tw_rows),
            institutions=tuple(inst),
            sectors_up=tuple(up),
            sectors_down=tuple(down),
            events_today=tuple(_events(calendar, today)),
            watch_stocks=tuple(watch),
            etfs=tuple(etfs),
            us_movers=tuple(_us_movers(us_rank)),
            tw_movers=tuple(_tw_movers(tw_mom)),
            headlines=_headlines(news),
            flags=tuple(_flags(us_rows, tw_rows, etfs)),
            unavailable=tuple(c.unavailable),
            us_change_series=_us_change_series(us_rows),
            sector_series=_bars([*up, *down]),
            flow_series=_flow_series(inst),
            watch_series=_indexed_lines([tw_hist, us_hist, tw_etf_hist, us_etf_hist]),
        )
        draft = draft.model_copy(update={"chart_hints": _chart_hints(draft)})
        return draft.model_copy(update={"report": render_morning_report(draft)})


def build_registration(
    registry: ToolRegistry, router: DataAccessRouter | None = None
) -> GetMorningBriefTool:
    return GetMorningBriefTool(registry, router)


__all__ = [
    "GetMorningBriefOutput",
    "GetMorningBriefTool",
    "MorningBriefInput",
    "build_registration",
]
