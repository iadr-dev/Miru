"""The weekly watchlist review: the week's close-to-close move of each of the user's own symbols,
the events due in the next seven days, a freshness line, and the plain-text report built from them.

Everything here is pure (no I/O, no clock): the tool reads the stored closes and event rows and
hands them in, so every figure is computed in code from stored data and none is written by a model.
The report is descriptive only - it lists what moved and what is scheduled, never what to do about
it.

Conventions:

- The week is the latest stored trading day of the market ("end") against the last trading day on
  or before seven calendar days earlier ("base"). Run on a weekend that is Friday against the
  previous Friday; a holiday week moves both ends to the nearest trading day, never to a guess.
- Stored closes are not adjusted for splits, par-value changes or capital reductions. A symbol
  whose window holds a split-like break is flagged, shown with its raw figure and left out of the
  best/worst lists, because the break would dominate them.
- The event window is the run day and the six days after it (seven days, both ends inclusive).
"""

from __future__ import annotations

from calendar.rules import financial_statement_deadline, monthly_revenue_deadline
from collections.abc import Iterable, Sequence
from datetime import date as date_
from datetime import timedelta
from itertools import pairwise
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import Market

WEEK_DAYS: Final = 7
EVENT_WINDOW_DAYS: Final = 7
DEFAULT_EXTREMES: Final = 3
# A one-day move larger than this is a split-like break, not a market move: Taiwan limits a day to
# about 10%; the US has no limit, so only a 2:1 or larger split is caught there.
BREAK_THRESHOLD: Final[dict[Market, float]] = {Market.TW: 0.105, Market.US: 0.45}

MoveStatus = Literal["ok", "no_data", "no_end_price", "no_base_price"]
EventKind = Literal[
    "ex_dividend",
    "ex_rights",
    "shareholder_meeting",
    "capital_increase",
    "disposition_start",
    "disposition_end",
    "financial_deadline",
    "revenue_deadline",
]
StatusKind = Literal["attention_listed", "disposition_active"]

_KIND_LABEL: Final[dict[str, str]] = {
    "ex_dividend": "除息",
    "ex_rights": "除權",
    "shareholder_meeting": "股東會",
    "capital_increase": "現金增資認股基準日",
    "disposition_start": "處置期間開始",
    "disposition_end": "處置期間結束",
    "financial_deadline": "財報法定公告期限",
    "revenue_deadline": "月營收法定申報期限",
}
_MARKET_LABEL: Final[dict[Market, str]] = {Market.TW: "台股", Market.US: "美股"}

SOURCE_DIVIDEND: Final = "股利公告資料"
SOURCE_CALENDAR: Final = "交易所行事曆"
SOURCE_DISPOSITION: Final = "交易所處置公告"
SOURCE_ATTENTION: Final = "交易所注意股公告"
SOURCE_RULE: Final = "法定申報期限（依規則推算）"
SOURCE_US_DIVIDEND: Final = "股利資料"

NOT_COVERED_TW: Final = "庫藏股執行期間、法說會日期、個別公司的財報公布日（目前沒有對應資料來源）"
NOT_COVERED_US: Final = "美股的財報公布日、股東會與庫藏股（目前沒有對應資料來源）"

FOOTER: Final = "以上為資料整理，非即時報價，非投資建議。"
NOTHING_TO_REVIEW: Final = "本週沒有可回顧的自選股：你的自選股群組目前是空的。"


class WeeklyMove(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str | None = None
    status: MoveStatus
    end_date: date_ | None = None
    end_close: float | None = None
    base_date: date_ | None = None
    base_close: float | None = None
    change_pct: float | None = None
    # A split-like one-day break sits inside the window: the change is the raw, unadjusted figure.
    basis_break: bool = False


class ReviewEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str | None = Field(default=None, description="None = applies to the whole market.")
    market: Market
    name: str | None = None
    kind: EventKind
    event_date: date_
    source: str
    detail: str | None = None


class ReviewStatus(BaseModel):
    """A standing condition (not a dated future event) that is true as of the data date."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    name: str | None = None
    kind: StatusKind
    since: date_
    until: date_ | None = None
    source: str


class Freshness(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    data_as_of: date_ | None = None
    base_date: date_ | None = None
    expected_as_of: date_ | None = None
    stale: bool = False


class SourceIssue(BaseModel):
    """A source that could not be read - said plainly in the report, never silently omitted."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    market: Market
    detail: str


class MarketReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Market
    symbols_total: int
    symbols_covered: int
    moves: tuple[WeeklyMove, ...] = Field(default_factory=tuple)
    events: tuple[ReviewEvent, ...] = Field(default_factory=tuple)
    statuses: tuple[ReviewStatus, ...] = Field(default_factory=tuple)
    freshness: Freshness
    issues: tuple[SourceIssue, ...] = Field(default_factory=tuple)


class WeeklyReview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_date: date_
    window_start: date_
    window_end: date_
    groups: int
    symbols_total: int
    symbols_covered: int
    markets: tuple[MarketReview, ...] = Field(default_factory=tuple)
    top_n: int = DEFAULT_EXTREMES


# ---------------------------------------------------------------------------------------------
# The week's move
# ---------------------------------------------------------------------------------------------


def week_bounds(market_dates: Iterable[date_], as_of: date_) -> tuple[date_ | None, date_ | None]:
    """`(base, end)` trading days for the week ending at the latest stored trading day on or before
    `as_of`: `end` is that day, `base` the last stored day on or before `end - 7 days`. Either is
    None when the stored history does not reach that far."""
    days = sorted(day for day in set(market_dates) if day <= as_of)
    if not days:
        return None, None
    end = days[-1]
    earlier = [day for day in days if day <= end - timedelta(days=WEEK_DAYS)]
    return (earlier[-1] if earlier else None), end


def has_basis_break(bars: Sequence[tuple[date_, float]], market: Market) -> bool:
    """True when two consecutive closes differ by more than the market's split-like threshold."""
    ordered = sorted(bars, key=lambda bar: bar[0])
    limit = BREAK_THRESHOLD.get(market, BREAK_THRESHOLD[Market.US])
    for (_, previous), (_, current) in pairwise(ordered):
        if previous > 0 and current > 0 and abs(current / previous - 1) > limit:
            return True
    return False


def weekly_move(  # noqa: PLR0913 — one keyword per input of the pure computation
    symbol: str,
    market: Market,
    bars: Sequence[tuple[date_, float]],
    *,
    base_date: date_ | None,
    end_date: date_ | None,
    name: str | None = None,
) -> WeeklyMove:
    """The close-to-close move of one symbol between the market's `base_date` and `end_date`.

    `bars` are the symbol's stored `(trade_date, close)` pairs around the window. A missing close
    on either day is a status, not a zero: `no_end_price` (nothing on the latest day, e.g. a
    suspension), `no_base_price` (nothing a week earlier, e.g. a new listing) and `no_data`
    (no closes at all)."""
    closes = dict(bars)

    def result(status: MoveStatus, **fields: object) -> WeeklyMove:
        return WeeklyMove.model_validate(
            {"symbol": symbol, "market": market, "name": name, "status": status, **fields}
        )

    if not closes or end_date is None:
        return result("no_data")
    end_close = closes.get(end_date)
    if end_close is None or end_close <= 0:
        return result("no_end_price", end_date=end_date, base_date=base_date)
    base_close = closes.get(base_date) if base_date is not None else None
    if base_date is None or base_close is None or base_close <= 0:
        return result("no_base_price", end_date=end_date, end_close=end_close, base_date=base_date)
    in_window = [(d, c) for d, c in closes.items() if base_date <= d <= end_date]
    return result(
        "ok",
        end_date=end_date,
        end_close=end_close,
        base_date=base_date,
        base_close=base_close,
        change_pct=round((end_close / base_close - 1) * 100, 2),
        basis_break=has_basis_break(in_window, market),
    )


def select_extremes(
    moves: Iterable[WeeklyMove], n: int = DEFAULT_EXTREMES
) -> tuple[tuple[WeeklyMove, ...], tuple[WeeklyMove, ...]]:
    """`(best, worst)`: up to `n` biggest gainers and `n` biggest decliners among the comparable
    moves (status ok, no basis break). A symbol is only a gainer with a positive change and only a
    decliner with a negative one, so a flat symbol is in neither list and nothing is in both."""
    ranked = sorted(
        (m for m in moves if m.status == "ok" and not m.basis_break and m.change_pct is not None),
        key=lambda m: (m.change_pct or 0.0, m.symbol),
    )
    best = tuple(m for m in reversed(ranked) if (m.change_pct or 0.0) > 0)[:n]
    worst = tuple(m for m in ranked if (m.change_pct or 0.0) < 0)[:n]
    return best, worst


def freshness_of(
    market: Market,
    end_date: date_ | None,
    expected: date_ | None,
    base_date: date_ | None = None,
) -> Freshness:
    """Data is stale when the newest stored trading day is older than the one the run day expects
    (the last trading day before it), or when there is no stored day at all."""
    stale = end_date is None or (expected is not None and end_date < expected)
    return Freshness(
        market=market,
        data_as_of=end_date,
        base_date=base_date,
        expected_as_of=expected,
        stale=stale,
    )


# ---------------------------------------------------------------------------------------------
# Events due in the next seven days
# ---------------------------------------------------------------------------------------------


def event_window(run_date: date_, days: int = EVENT_WINDOW_DAYS) -> tuple[date_, date_]:
    """The run day and the following `days - 1` days, both ends inclusive."""
    return run_date, run_date + timedelta(days=days - 1)


def select_events(
    events: Iterable[ReviewEvent], start: date_, end: date_
) -> tuple[ReviewEvent, ...]:
    """Events inside `[start, end]` (inclusive), one per `(symbol, kind, date)`, oldest first."""
    unique: dict[tuple[str | None, str, date_], ReviewEvent] = {}
    for event in events:
        if start <= event.event_date <= end:
            unique.setdefault((event.symbol, event.kind, event.event_date), event)
    return tuple(
        sorted(unique.values(), key=lambda e: (e.event_date, e.symbol or "", e.kind, e.source))
    )


def statutory_deadlines(start: date_, end: date_, market: Market = Market.TW) -> list[ReviewEvent]:
    """The 財報 and 月營收 statutory filing deadlines that fall inside `[start, end]`. They apply to
    every listed company, not to one stock, and say nothing about when a given company reports."""
    found: list[ReviewEvent] = []
    for year in range(start.year - 1, end.year + 1):
        for quarter in range(1, 5):
            day = financial_statement_deadline(year, quarter)
            if start <= day <= end:
                label = "年報" if quarter == 4 else f"第{quarter}季財報"  # noqa: PLR2004
                found.append(
                    ReviewEvent(
                        market=market,
                        kind="financial_deadline",
                        event_date=day,
                        source=SOURCE_RULE,
                        detail=f"{label}（上市櫃公司）",
                    )
                )
    year, month = (start.year, start.month - 1) if start.month > 1 else (start.year - 1, 12)
    while (year, month) <= (end.year, end.month):
        day = monthly_revenue_deadline(year, month)
        if start <= day <= end:
            found.append(
                ReviewEvent(
                    market=market,
                    kind="revenue_deadline",
                    event_date=day,
                    source=SOURCE_RULE,
                    detail=f"{month} 月營收",
                )
            )
        year, month = (year, month + 1) if month < 12 else (year + 1, 1)  # noqa: PLR2004
    return found


# ---------------------------------------------------------------------------------------------
# The report text
# ---------------------------------------------------------------------------------------------


def _who(symbol: str | None, name: str | None) -> str:
    if symbol is None:
        return "全市場"
    return f"{symbol} {name}" if name else symbol


def _price(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:+.2f}%"


def _move_line(move: WeeklyMove) -> str:
    who = _who(move.symbol, move.name)
    return f"{who} {_pct(move.change_pct)}（{_price(move.base_close)} → {_price(move.end_close)}）"


def _gap_reason(move: WeeklyMove) -> str:
    if move.status == "no_data":
        return "查無收盤資料"
    if move.status == "no_end_price":
        return f"{move.end_date} 沒有收盤價（可能停牌）" if move.end_date else "沒有最新收盤價"
    return "前一週沒有收盤價（可能是新掛牌）"


def _event_line(event: ReviewEvent) -> str:
    label = _KIND_LABEL[event.kind]
    detail = f"，{event.detail}" if event.detail else ""
    return f"{event.event_date.isoformat()} {_who(event.symbol, event.name)} {label}{detail}（來源：{event.source}）"  # noqa: E501


def _status_line(status: ReviewStatus) -> str:
    who = _who(status.symbol, status.name)
    if status.kind == "attention_listed":
        return f"{who} 近期列入注意股（公告日 {status.since.isoformat()}，來源：{status.source}）"
    until = f"，期間至 {status.until.isoformat()}" if status.until else ""
    return f"{who} 處置期間中（自 {status.since.isoformat()}{until}，來源：{status.source}）"


def _freshness_line(fresh: Freshness) -> str:
    if fresh.data_as_of is None:
        return "資料日期：目前沒有可用的收盤資料。"
    line = f"資料日期：收盤資料截至 {fresh.data_as_of.isoformat()}"
    if fresh.stale and fresh.expected_as_of is not None:
        line += f"（較預期的 {fresh.expected_as_of.isoformat()} 舊，資料可能尚未更新）"
    return line + "。"


def _market_section(section: MarketReview, window: tuple[date_, date_], top_n: int) -> list[str]:
    label = _MARKET_LABEL.get(section.market, section.market.value)
    covered = (
        f"{section.symbols_total} 檔"
        if section.symbols_covered == section.symbols_total
        else f"{section.symbols_total} 檔，本次涵蓋 {section.symbols_covered} 檔"
    )
    lines = ["", f"### {label}（{covered}）"]
    fresh = section.freshness
    ok = [m for m in section.moves if m.status == "ok"]
    gaps = [m for m in section.moves if m.status != "ok"]
    if ok and fresh.base_date and fresh.data_as_of:
        lines.append(
            f"**一週變動**（{fresh.data_as_of.isoformat()} 收盤對 {fresh.base_date.isoformat()} "
            "收盤，價格未還原權值）"
        )
        best, worst = select_extremes(section.moves, top_n)
        if best:
            lines.append("- 漲幅居前：" + "；".join(_move_line(m) for m in best))
        if worst:
            lines.append("- 跌幅居前：" + "；".join(_move_line(m) for m in worst))
        if not best and not worst:
            lines.append("- 本週沒有明顯漲跌的標的（皆持平或價格基準有異動）。")
        breaks = [m for m in ok if m.basis_break]
        if breaks:
            names = "、".join(_who(m.symbol, m.name) for m in breaks)
            lines.append(
                "- 價格基準可能受分割、減資或面額變更影響，以下為未還原的原始數字，不列入排行："
                f"{names}"
            )
        lines.append(f"- 已計算週變動：{len(ok)} 檔。")
    else:
        lines.append("**一週變動**：目前無法計算（缺少足夠的收盤資料）。")
    if gaps:
        lines.append(
            f"- 無法計算：{len(gaps)} 檔 — "
            + "；".join(f"{_who(m.symbol, m.name)}（{_gap_reason(m)}）" for m in gaps)
        )
    lines.append(f"**未來 7 天事件**（{window[0].isoformat()} 至 {window[1].isoformat()}）")
    if section.events:
        lines.extend(f"- {_event_line(e)}" for e in section.events)
    else:
        lines.append("- 查無已公布的事件。")
    if section.statuses:
        lines.append("**目前狀態**")
        lines.extend(f"- {_status_line(s)}" for s in section.statuses)
    lines.append(_freshness_line(fresh))
    lines.extend(
        f"- 注意：{issue.source}暫時無法讀取（{issue.detail}），相關事件可能不完整。"
        for issue in section.issues
    )
    return lines


def render_report(review: WeeklyReview) -> str:
    """The full report text. Every figure is read from `review`; nothing is estimated."""
    if review.symbols_total == 0:
        return f"{NOTHING_TO_REVIEW}\n{FOOTER}"
    head = f"自選股週回顧（{review.run_date.isoformat()}）"
    scope = f"範圍：{review.groups} 個群組，共 {review.symbols_total} 檔"
    if review.symbols_covered < review.symbols_total:
        scope += (
            f"；本次涵蓋 {review.symbols_covered} 檔，其餘 "
            f"{review.symbols_total - review.symbols_covered} 檔超過單次上限而未納入"
        )
    lines = [head, f"重點：{render_notice(review)}", scope + "。"]
    window = (review.window_start, review.window_end)
    for section in review.markets:
        lines.extend(_market_section(section, window, review.top_n))
    markets = {section.market for section in review.markets}
    not_covered = [
        text
        for market, text in ((Market.TW, NOT_COVERED_TW), (Market.US, NOT_COVERED_US))
        if market in markets
    ]
    if not_covered:
        lines += ["", "尚未涵蓋：" + "；".join(not_covered) + "。"]
    lines += ["", "完整內容請見此對話。", FOOTER]
    return "\n".join(lines)


def render_notice(review: WeeklyReview) -> str:
    """The one-paragraph summary a notification carries (the full report is in the thread)."""
    if review.symbols_total == 0:
        return NOTHING_TO_REVIEW
    parts = [f"共 {review.symbols_total} 檔"]
    for section in review.markets:
        label = _MARKET_LABEL.get(section.market, section.market.value)
        best, worst = select_extremes(section.moves, 1)
        bits = [f"{label}未來 7 天事件 {len(section.events)} 項"]
        if best:
            bits.append(
                f"週漲幅居前 {_who(best[0].symbol, best[0].name)} {_pct(best[0].change_pct)}"
            )
        if worst:
            bits.append(
                f"跌幅居前 {_who(worst[0].symbol, worst[0].name)} {_pct(worst[0].change_pct)}"
            )
        if section.freshness.stale:
            bits.append("資料較舊")
        parts.append("、".join(bits))
    return "；".join(parts) + "。"


__all__ = [
    "BREAK_THRESHOLD",
    "DEFAULT_EXTREMES",
    "EVENT_WINDOW_DAYS",
    "FOOTER",
    "NOTHING_TO_REVIEW",
    "NOT_COVERED_TW",
    "NOT_COVERED_US",
    "SOURCE_ATTENTION",
    "SOURCE_CALENDAR",
    "SOURCE_DISPOSITION",
    "SOURCE_DIVIDEND",
    "SOURCE_RULE",
    "SOURCE_US_DIVIDEND",
    "WEEK_DAYS",
    "Freshness",
    "MarketReview",
    "ReviewEvent",
    "ReviewStatus",
    "SourceIssue",
    "WeeklyMove",
    "WeeklyReview",
    "event_window",
    "freshness_of",
    "has_basis_break",
    "render_notice",
    "render_report",
    "select_events",
    "select_extremes",
    "statutory_deadlines",
    "week_bounds",
    "weekly_move",
]
