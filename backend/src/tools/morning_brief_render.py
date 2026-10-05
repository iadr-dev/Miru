"""Renders the morning brief as the final 晨間摘要 text — by code, not by a model.

The numbers were already assembled by `get_morning_brief`; this turns them into the report so the
wording cannot drift: every sentence is a fixed template over a tool figure, so there is no
interpretation to drift into advice, no invented number and no stray conclusion. It is also why the
scheduled 晨間摘要 costs no model call.

Layout follows the merged morning-report spec — snapshot, top drivers, Taiwan pre-open, US watch,
ETF corner, the user's own symbols, headlines, anomalies/data gaps and today's observation points —
minus what governance rules out (trading levels, confirm/invalidate lines, "sectors to watch",
predicted opens). "Top 3 Drivers" are the three largest moves by a fixed, documented ranking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tools.morning_brief_models import BriefEtf, BriefRow, GetMorningBriefOutput

_NA = "N/A"
_WEEKDAYS = ("一", "二", "三", "四", "五", "六", "日")
_FOOTER = "以上為資料整理，非即時報價，不構成買賣建議。"
_MAX_DRIVERS = 3
_MAX_WATCH_POINTS = 3


def _signed(value: float | None) -> str:
    if value is None:
        return _NA
    rounded = round(value, 1)
    return f"{rounded + 0.0:+.1f}%" if rounded else "0.0%"  # never "-0.0%"


def _cell(text: str) -> str:
    return text.replace("|", "¦").replace("\n", " ").strip() or _NA


def _table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
        *("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows),
    ]
    return "\n".join(lines)


def _level_rows(rows: tuple[BriefRow, ...]) -> list[tuple[str, ...]]:
    return [(r.label, r.text, _signed(r.change_pct), r.note or "—") for r in rows]


# ---- Top 3 drivers ------------------------------------------------------------------------


@dataclass(frozen=True)
class _Driver:
    score: float
    line: str


# How large a move counts as "one unit" of significance for each kind of series, in percent (or 億
# for net flows). Dividing by it puts a 2% Nasdaq day, a 10% VIX day and a 50 億 institution day on
# one scale so the largest three can be picked by rule.
_UNIT = {"index": 1.0, "sox": 2.0, "vix": 10.0, "adr": 2.0, "yield": 2.0, "flow": 50.0}


def _kind(label: str) -> str | None:
    name = label.upper()
    if name.startswith("VIX"):
        return "vix"
    if "SOX" in name or "半導體" in label:
        return "sox"
    if name.startswith(("S&P", "NASDAQ", "DOW")):
        return "index"
    if "殖利率" in label:
        return "yield"
    return None


_LINK = {
    "index": "美股大盤走勢指標 → 相關：美股大盤與追蹤大盤的 ETF",
    "sox": "半導體產業的美股參考指數 → 相關：台積電 ADR 與台股半導體權值股",
    "vix": "S&P 500 選擇權隱含波動的指標 → 相關：美股大盤",
    "yield": "美國公債 10 年期殖利率 → 相關：美債與利率敏感資產",
}


def _drivers(brief: GetMorningBriefOutput) -> list[str]:
    found: list[_Driver] = []
    for r in brief.us_overnight:
        kind = _kind(r.label)
        if kind is None or r.change_pct is None:
            continue
        line = f"{r.label} {_signed(r.change_pct)}（{r.text}） → {_LINK[kind]}"
        found.append(_Driver(abs(r.change_pct) / _UNIT[kind], line))
    for r in brief.tw_market:
        if r.change_pct is None:
            continue
        if "ADR" in r.label:
            line = (
                f"{r.label} {_signed(r.change_pct)}（{r.text}） → 台積電在美國掛牌的存託憑證，"
                "為前一交易日收盤 → 相關：台股 2330"
            )
            found.append(_Driver(abs(r.change_pct) / _UNIT["adr"], line))
        elif "TAIEX" in r.label:
            line = (
                f"{r.label} {_signed(r.change_pct)}（{r.text}） → 台股大盤 → "
                "相關：台股現貨與大盤 ETF"
            )
            found.append(_Driver(abs(r.change_pct) / _UNIT["index"], line))
    for r in brief.institutions:
        amount = _yi(r.text)
        if amount is not None:
            direction = "買超" if amount >= 0 else "賣超"
            line = (
                f"{r.label}{direction} {abs(amount):,.1f} 億元（{r.note.split(' ')[0]}） → "
                "三大法人單日買賣超 → 相關：台股現貨"
            )
            found.append(_Driver(abs(amount) / _UNIT["flow"], line))
    ranked = sorted(found, key=lambda d: d.score, reverse=True)[:_MAX_DRIVERS]
    return [d.line for d in ranked]


def _yi(text: str) -> float | None:
    try:
        return float(text.replace("億元", "").replace(",", "").strip())
    except ValueError:
        return None


# ---- sections -----------------------------------------------------------------------------


def _etf_rows(etfs: tuple[BriefEtf, ...]) -> list[tuple[str, ...]]:
    return [
        (
            f"{e.symbol}（{'台股' if e.market.value == 'TW' else '美股'}）",
            e.close,
            _signed(e.change_pct),
            _signed(e.premium_pct) if e.premium_pct is not None else "—",
            e.note,
        )
        for e in etfs
    ]


def _watch_points(brief: GetMorningBriefOutput) -> list[str]:
    return [
        f"{e.taipei_time} 台北、{e.et_time} 美東：{e.name}（預期值 {e.consensus}，前值 {e.prior}）"
        for e in brief.events_today[:_MAX_WATCH_POINTS]
    ]


def _weekday(day: str) -> str:
    try:
        return "星期" + _WEEKDAYS[date.fromisoformat(day).weekday()]
    except ValueError:
        return ""


def _snapshot(brief: GetMorningBriefOutput) -> list[str]:
    rows = _level_rows(brief.us_overnight) + _level_rows(brief.macro)
    headers = ("資產", "收盤或數值", "漲跌幅", "資料日或備註")
    return ["### Overnight Snapshot 隔夜概況", "", _table(headers, rows) if rows else _NA, ""]


def _top_drivers(brief: GetMorningBriefOutput) -> list[str]:
    lines = [f"{i}. {line}" for i, line in enumerate(_drivers(brief), 1)]
    return ["### Top 3 Drivers 三大重點", "", *(lines or [_NA]), ""]


def _taiwan(brief: GetMorningBriefOutput) -> list[str]:
    out = ["### 台股 Pre-Open", ""]
    if brief.tw_market:
        headers = ("項目", "數值", "漲跌幅", "資料日或備註")
        out += [_table(headers, _level_rows(brief.tw_market)), ""]
    if brief.institutions:
        flows = "、".join(f"{r.label} {r.text}" for r in brief.institutions)
        out += [f"三大法人（{brief.institutions[0].note.split(' ')[0]}）：{flows}", ""]
    else:
        out += ["三大法人：N/A", ""]
    for title, rows in (("類股漲幅居前", brief.sectors_up), ("類股跌幅居前", brief.sectors_down)):
        if rows:
            table = _table(
                ("類股", "漲跌幅", "家數（資料日）"), [(r.label, r.text, r.note) for r in rows]
            )
            out += [f"**{title}**", "", table, ""]
    if brief.tw_movers:
        table = _table(
            ("標的", "漲跌幅", "註記"), [(r.label, r.text, r.note) for r in brief.tw_movers]
        )
        out += ["**台股個別漲跌幅**", "", table, ""]
    return out


def _us_watch(brief: GetMorningBriefOutput) -> list[str]:
    out = ["### US Watch", ""]
    if brief.events_today:
        rows = [
            (e.name, e.taipei_time, e.et_time, e.consensus, e.prior) for e in brief.events_today
        ]
        out += [_table(("事件", "台北時間", "美東時間", "預期值", "前值"), rows), ""]
    else:
        out += ["今日無已排定的重要數據發布。", ""]
    if brief.us_movers:
        rows = [(r.label, r.text, _signed(r.change_pct)) for r in brief.us_movers]
        out += ["**美股熱門個股**", "", _table(("標的", "價格", "漲跌幅"), rows), ""]
    return out


def _etf_corner(brief: GetMorningBriefOutput) -> list[str]:
    if not brief.etfs:
        return ["### ETF Corner", "", "未追蹤 ETF。", ""]
    headers = ("ETF", "收盤", "漲跌幅", "折溢價", "備註")
    return ["### ETF Corner", "", _table(headers, _etf_rows(brief.etfs)), ""]


def _watchlist(brief: GetMorningBriefOutput) -> list[str]:
    if not brief.watch_stocks:
        return []
    rows = [(r.label, r.text, _signed(r.change_pct), r.note) for r in brief.watch_stocks]
    return ["### 自選標的", "", _table(("標的", "收盤", "漲跌幅", "備註"), rows), ""]


def _headlines(brief: GetMorningBriefOutput) -> list[str]:
    if not brief.headlines:
        return []
    items = [f"- {h.title}（{h.source}）" if h.source else f"- {h.title}" for h in brief.headlines]
    return ["### 新聞標題（第三方，僅列標題與來源）", "", *items, ""]


def _anomalies(brief: GetMorningBriefOutput) -> list[str]:
    flags = [f"- {f}" for f in brief.flags] or ["- 今日無觸發異常提示。"]
    gaps = [f"- 資料缺口：{gap}" for gap in brief.unavailable]
    return ["### 異常與資料缺口", "", *flags, *gaps, ""]


def _observation_points(brief: GetMorningBriefOutput) -> list[str]:
    points = _watch_points(brief)
    if not points:
        return []
    return ["### 今日觀察點（事件與數據發布時點）", "", *(f"- {p}" for p in points), ""]


def render_morning_report(brief: GetMorningBriefOutput) -> str:
    """The complete 晨間摘要 as markdown."""
    day = f"{brief.date_taipei}（{_weekday(brief.date_taipei)}）"
    title = f"**【晨間摘要】{day}{brief.time_taipei} 台北**"
    sections = (
        [title, "", f"_{brief.caveat}_", ""],
        _snapshot(brief),
        _top_drivers(brief),
        _taiwan(brief),
        _us_watch(brief),
        _etf_corner(brief),
        _watchlist(brief),
        _headlines(brief),
        _anomalies(brief),
        _observation_points(brief),
        [_FOOTER],
    )
    return chr(10).join(line for section in sections for line in section)


__all__ = ["render_morning_report"]
