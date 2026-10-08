"""Pure alert-template evaluation over the ingested derived store.

Reads only `(dataset, market, date)` partitions through the read-only `SnapshotReader` seam
(`ingest.reader.ParquetMarketDataReader` satisfies it structurally) — never a source adapter and
never a per-user upstream fetch, so evaluation spends no FinMind budget (modules/14). Reading is
blocking DuckDB work; the worker runs a whole evaluation pass in a thread and serialises it, since
one reader connection must not be shared across concurrent queries.

`evaluate_condition` is a function of the data and the rule alone: it returns `met` and a
fact-phrased message, or `None` when the symbol has no row for `as_of` (absence is never a trigger).
Edge-triggering, cooldown and delivery are the worker's concern.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from scheduler.templates import (
    AttentionListEntry,
    ClosePrice,
    ConditionModel,
    DailyChange,
    DispositionListEntry,
    EtfPremiumDiscount,
    InstitutionalStreak,
    MarginBalanceChange,
    MaterialAnnouncementPublished,
    MonthRevenuePublished,
    VolumeSurge,
    dataset_of,
    describe,
    history_days_of,
    investor_label,
)
from schemas.enums import Market

Row = dict[str, object]

# Calendar-day lookback cap when walking back for N prior trading days — generous enough to span
# weekends and long holidays, bounded so a sparse store can never trigger an unbounded scan.
_LOOKBACK_FACTOR = 3
_LOOKBACK_PAD = 10


class SnapshotReader(Protocol):
    def read_partition(self, dataset: str, market: str, run_date: date) -> list[Row]: ...

    def has_partition(self, dataset: str, market: str, run_date: date) -> bool: ...


class Evaluation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    met: bool
    message: str
    # A version of `message` without any third-party text, for when the scan rejects the quote.
    plain_message: str | None = None


class SnapshotView:
    """Caches per-`(dataset, market, date)` partitions indexed by symbol for one evaluation pass,
    so N rules over the same partition read it once."""

    def __init__(self, reader: SnapshotReader) -> None:
        self._reader = reader
        self._cache: dict[tuple[str, str, date], dict[str, list[Row]]] = {}

    def latest_partition_date(
        self, dataset: str, market: Market, *, on_or_before: date, max_back_days: int = 10
    ) -> date | None:
        for offset in range(max_back_days + 1):
            day = on_or_before - timedelta(days=offset)
            if self._reader.has_partition(dataset, market.value, day):
                return day
        return None

    def rows(self, dataset: str, market: Market, day: date, symbol: str) -> list[Row]:
        key = (dataset, market.value, day)
        if key not in self._cache:
            indexed: dict[str, list[Row]] = {}
            for row in self._reader.read_partition(dataset, market.value, day):
                indexed.setdefault(str(row.get("symbol")), []).append(row)
            self._cache[key] = indexed
        return self._cache[key].get(symbol, [])

    def prior_dates(self, dataset: str, market: Market, before: date, count: int) -> list[date]:
        """Up to `count` most recent dates before `before` that have a partition, newest first."""
        found: list[date] = []
        limit = count * _LOOKBACK_FACTOR + _LOOKBACK_PAD
        for offset in range(1, limit + 1):
            if len(found) >= count:
                break
            day = before - timedelta(days=offset)
            if self._reader.has_partition(dataset, market.value, day):
                found.append(day)
        return found


def _num(row: Row, column: str) -> float | None:
    raw = row.get(column)
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else None


def _close(
    view: SnapshotView, market: Market, day: date, symbol: str, dataset: str
) -> float | None:
    rows = view.rows(dataset, market, day, symbol)
    return _num(rows[0], "close") if rows else None


def _net(rows: list[Row], investor: str) -> float:
    prefix = {"foreign": "Foreign", "trust": "Investment_Trust", "dealer": "Dealer"}[investor]
    total = 0.0
    for row in rows:
        name = str(row.get("investor", ""))
        if name.startswith(prefix):
            total += (_num(row, "buy") or 0.0) - (_num(row, "sell") or 0.0)
    return total


def _message(symbol: str, fact: str, condition: ConditionModel) -> str:
    return f"{symbol} {fact}（你設定的條件：{describe(condition)}）"


def _eval_close(c: ClosePrice, symbol: str, close: float) -> Evaluation:
    met = close > c.value if c.comparator == "above" else close < c.value
    relation = "高於" if c.comparator == "above" else "低於"
    return Evaluation(
        met=met, message=_message(symbol, f"收盤價 {close:g}，{relation}設定值 {c.value:g}", c)
    )


def evaluate_condition(  # noqa: PLR0911 — one early return per template kind
    condition: ConditionModel,
    *,
    symbol: str,
    market: Market,
    as_of: date,
    view: SnapshotView,
) -> Evaluation | None:
    dataset = dataset_of(condition, market)
    today = view.rows(dataset, market, as_of, symbol)

    if isinstance(condition, AttentionListEntry):
        # The partition is the day's flag list, so presence IS the observation; a symbol that is
        # absent is a legitimate "not flagged" (met=False), not missing data.
        if not view.latest_partition_date(dataset, market, on_or_before=as_of, max_back_days=0):
            return None
        met = bool(today)
        return Evaluation(met=met, message=_message(symbol, "列入注意股名單", condition))

    if isinstance(condition, DispositionListEntry):
        return _eval_disposition(condition, symbol=symbol, market=market, as_of=as_of, view=view)

    if isinstance(condition, (MarginBalanceChange, EtfPremiumDiscount)):
        return _eval_single_row(condition, symbol, market, as_of, view)

    if isinstance(condition, MaterialAnnouncementPublished):
        return _eval_announcement(condition, symbol=symbol, market=market, as_of=as_of, view=view)

    if isinstance(condition, MonthRevenuePublished):
        return _eval_month_revenue(condition, symbol=symbol, market=market, as_of=as_of, view=view)

    if not today:
        return None

    if isinstance(condition, ClosePrice):
        close = _num(today[0], "close")
        return None if close is None else _eval_close(condition, symbol, close)

    if isinstance(condition, DailyChange):
        close = _num(today[0], "close")
        prior = view.prior_dates(dataset, market, as_of, history_days_of(condition))
        prev = _close(view, market, prior[0], symbol, dataset) if prior else None
        if close is None or prev is None or prev == 0:
            return None
        change = (close - prev) / prev * 100
        met = (
            change >= condition.percent
            if condition.direction == "up"
            else (change <= -condition.percent)
        )
        return Evaluation(
            met=met,
            message=_message(
                symbol, f"單日變動 {change:+.2f}%（前一交易日收盤 {prev:g}）", condition
            ),
        )

    if isinstance(condition, VolumeSurge):
        volume = _num(today[0], "volume")
        prior = view.prior_dates(dataset, market, as_of, condition.window)
        history = [
            v
            for d in prior
            if (rows := view.rows(dataset, market, d, symbol))
            and (v := _num(rows[0], "volume")) is not None
        ]
        if volume is None or len(history) < condition.window // 2 or sum(history) == 0:
            return None
        ratio = volume / (sum(history) / len(history))
        return Evaluation(
            met=ratio >= condition.multiple,
            message=_message(
                symbol, f"成交量為近 {len(history)} 日均量的 {ratio:.1f} 倍", condition
            ),
        )

    return _eval_streak(condition, symbol=symbol, market=market, as_of=as_of, view=view)


def _day_text(value: object) -> str:
    """A date-ish cell (ISO string or date) as its `YYYY-MM-DD` text, for comparison/display."""
    return str(value)[:10] if value is not None else ""


def _eval_disposition(
    condition: DispositionListEntry,
    *,
    symbol: str,
    market: Market,
    as_of: date,
    view: SnapshotView,
) -> Evaluation | None:
    """處置股 is a per-day snapshot list, so an ingested partition with the symbol absent is a real
    "not listed"; only a day with no partition at all is unevaluable (same contract as 注意股)."""
    dataset = dataset_of(condition, market)
    if view.latest_partition_date(dataset, market, on_or_before=as_of, max_back_days=0) is None:
        return None
    today = view.rows(dataset, market, as_of, symbol)
    if not today:
        return Evaluation(met=False, message=_message(symbol, "未列入處置股名單", condition))
    row = today[0]
    start, end = _day_text(row.get("period_start")), _day_text(row.get("period_end"))
    span = f"，處置期間 {start} 至 {end}" if start and end else ""
    return Evaluation(met=True, message=_message(symbol, f"列入處置股名單{span}", condition))


def _eval_month_revenue(
    condition: MonthRevenuePublished,
    *,
    symbol: str,
    market: Market,
    as_of: date,
    view: SnapshotView,
) -> Evaluation | None:
    """Monthly revenue is an event feed: on most days the day's partition simply has no row for a
    symbol. The row counts only if it is dated the evaluation day itself — this does not rely on
    the upstream returning a single day, so a feed that also echoes later rows cannot make an old
    filing look new."""
    dataset = dataset_of(condition, market)
    if view.latest_partition_date(dataset, market, on_or_before=as_of, max_back_days=0) is None:
        return None
    today = view.rows(dataset, market, as_of, symbol)
    todays = [r for r in today if _day_text(r.get("trade_date")) == as_of.isoformat()]
    if not todays:
        return Evaluation(met=False, message=_message(symbol, "本日未公布月營收", condition))
    row = todays[0]
    year, month = row.get("revenue_year"), row.get("revenue_month")
    label = f"{year} 年 {month} 月" if year and month else "最新"
    return Evaluation(met=True, message=_message(symbol, f"公布{label}月營收", condition))


# The issuer's subject line is third-party text. It is quoted as data, clipped, and stripped of
# anything that could be read as markup or a link by a channel that renders it.
_SUBJECT_MAX = 40
# Markup/link characters, the quote marks our own sentence uses, and sentence terminators (the
# output scan splits on those) become a space, so a subject can neither break out of its
# quotation nor read as sentences of ours.
_SUBJECT_DROP = {ord(ch): " " for ch in "<>[]()`*_#\\|「」『』\"'。！？!?；;"}


def _quoted_subject(raw: object) -> str:
    text = "".join(ch if ch.isprintable() else " " for ch in str(raw or ""))
    text = " ".join(text.translate(_SUBJECT_DROP).split())
    return text if len(text) <= _SUBJECT_MAX else f"{text[:_SUBJECT_MAX]}…"


def _eval_announcement(
    condition: MaterialAnnouncementPublished,
    *,
    symbol: str,
    market: Market,
    as_of: date,
    view: SnapshotView,
) -> Evaluation | None:
    """MOPS 重大訊息 is an event feed: most days a symbol has no row. Only rows announced on the
    evaluation day itself count, so a feed that echoes older filings cannot make one look new. The
    message states that filings exist and quotes the latest subject - it never characterises it."""
    dataset = dataset_of(condition, market)
    if view.latest_partition_date(dataset, market, on_or_before=as_of, max_back_days=0) is None:
        return None
    todays = [
        r
        for r in view.rows(dataset, market, as_of, symbol)
        if _day_text(r.get("announced_date")) == as_of.isoformat()
    ]
    if not todays:
        return Evaluation(met=False, message=_message(symbol, "本日無重大訊息", condition))
    latest = max(todays, key=lambda r: str(r.get("announced_time", "")))
    subject = _quoted_subject(latest.get("subject"))
    count = f"{len(todays)} 則" if len(todays) > 1 else "1 則"
    quote = f"，最新主旨：「{subject}」" if subject else ""
    return Evaluation(
        met=True,
        message=_message(symbol, f"公布 {count}重大訊息{quote}", condition),
        plain_message=_message(symbol, f"公布 {count}重大訊息", condition),
    )


def _eval_single_row(
    condition: MarginBalanceChange | EtfPremiumDiscount,
    symbol: str,
    market: Market,
    as_of: date,
    view: SnapshotView,
) -> Evaluation | None:
    """Templates computed from one row of the day's partition: no history, and a symbol with no row
    for the day (no margin account, or not an ETF with a NAV) is unevaluable, never "not met"."""
    rows = view.rows(dataset_of(condition, market), market, as_of, symbol)
    if not rows:
        return None
    if isinstance(condition, MarginBalanceChange):
        return _eval_margin(condition, symbol, rows[0])
    return _eval_premium(condition, symbol, rows[0])


def _eval_margin(condition: MarginBalanceChange, symbol: str, row: Row) -> Evaluation | None:
    prefix = "margin_purchase" if condition.side == "margin" else "short_sale"
    today = _num(row, f"{prefix}_today_balance")
    prior = _num(row, f"{prefix}_yesterday_balance")
    if today is None or prior is None or prior <= 0:
        return None  # no base to measure a percentage change against
    change = (today - prior) / prior * 100
    met = (
        change >= condition.percent if condition.direction == "up" else change <= -condition.percent
    )
    label = "融資" if condition.side == "margin" else "融券"
    return Evaluation(
        met=met,
        message=_message(
            symbol, f"{label}餘額由 {prior:,.0f} 變為 {today:,.0f}（{change:+.2f}%）", condition
        ),
    )


def _eval_premium(condition: EtfPremiumDiscount, symbol: str, row: Row) -> Evaluation | None:
    pct = _num(row, "premium_discount_pct")
    if pct is None:
        return None  # the issuer published no estimate for this fund today
    met = (
        pct >= condition.percent if condition.direction == "premium" else pct <= -condition.percent
    )
    close, nav = _num(row, "close_price"), _num(row, "estimated_nav")
    detail = (
        f"（成交價 {close:g}、預估淨值 {nav:g}）" if close is not None and nav is not None else ""
    )
    # The NAV is the issuer's provisional estimate relayed by TWSE, so the wording says so.
    return Evaluation(
        met=met, message=_message(symbol, f"預估折溢價幅度 {pct:+.2f}%{detail}", condition)
    )


def _eval_streak(
    condition: InstitutionalStreak,
    *,
    symbol: str,
    market: Market,
    as_of: date,
    view: SnapshotView,
) -> Evaluation:
    sign = 1 if condition.direction == "buy" else -1
    side = "淨買超" if condition.direction == "buy" else "淨賣超"
    label = investor_label(condition.investor)
    dataset = dataset_of(condition, market)

    streak = 0
    cumulative = 0.0
    rows_by_day = [view.rows(dataset, market, as_of, symbol)]
    for prior in view.prior_dates(dataset, market, as_of, history_days_of(condition)):
        rows_by_day.append(view.rows(dataset, market, prior, symbol))
    for rows in rows_by_day:
        net = _net(rows, condition.investor)
        if net * sign <= 0:
            break
        streak += 1
        cumulative += abs(net)
    met = streak >= condition.days
    fact = f"{label}連續 {streak} 日{side}" if streak else f"{label}當日未{side}"
    if met:
        fact += f"，累計 {cumulative:,.0f} 股"
    return Evaluation(met=met, message=_message(symbol, fact, condition))


__all__ = ["Evaluation", "SnapshotReader", "SnapshotView", "evaluate_condition"]
