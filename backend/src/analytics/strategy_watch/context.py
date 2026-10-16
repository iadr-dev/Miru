"""The layers a professional screen adds on top of a technical setup: who is buying (籌碼), how the
stock's sector is doing, and what event could make the levels meaningless (處置/注意股, an
ex-dividend gap, a material announcement). Pure functions over rows already read from the store.

Exclusions remove a stock from the watchlist; everything else is context attached to the alert,
never a condition the alert waits on.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date as date_
from datetime import datetime

from analytics.coerce import as_float, as_int
from analytics.indicators import PriceBar
from analytics.screener.batch import LARGE_HOLDER_TIER_LABELS
from analytics.screener.chip_streaks import streak_length
from analytics.strategy_watch.strategies import HOLDER_WEEKS, RANGE_WINDOW, ChipInputs

FOREIGN_INVESTOR = "Foreign_Investor"
INVESTMENT_TRUST = "Investment_Trust"
SECTOR_RETURN_WINDOW = 20
MARGIN_CHANGE_WINDOW = 5
HEADLINE_MAX_CHARS = 80
_TOTAL_LEVEL = "total"  # the shareholding-distribution row that carries the holder count


def as_date(value: object) -> date_ | None:
    """A stored date cell (a `date`, a pandas timestamp, or an ISO string) as a plain `date`."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date_):
        return value
    if isinstance(value, str) and value:
        try:
            return date_.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _net_by_investor(rows: Iterable[Mapping[str, object]]) -> dict[str, dict[date_, float]]:
    by_investor: dict[str, dict[date_, float]] = defaultdict(dict)
    for row in rows:
        investor = row.get("investor")
        day = as_date(row.get("trade_date"))
        if not isinstance(investor, str) or day is None:
            continue
        net = float(as_int(row.get("buy")) - as_int(row.get("sell")))
        by_investor[investor][day] = by_investor[investor].get(day, 0.0) + net
    return by_investor


def chip_streaks(rows: Iterable[Mapping[str, object]]) -> tuple[int, int]:
    """(外資, 投信) current consecutive net-buy (+N) or net-sell (-N) streak for one symbol."""
    by_investor = _net_by_investor(rows)

    def streak(investor: str) -> int:
        per_day = by_investor.get(investor, {})
        return streak_length([net for _, net in sorted(per_day.items())])

    return streak(FOREIGN_INVESTOR), streak(INVESTMENT_TRUST)


def margin_change_pct(
    rows: Iterable[Mapping[str, object]], window: int = MARGIN_CHANGE_WINDOW
) -> float | None:
    """融資餘額 change over the last `window` sessions, in percent. A fast-rising margin balance is
    retail crowding, which practitioners read as weaker hands behind a move."""
    balances = sorted(
        (day, balance)
        for row in rows
        if (day := as_date(row.get("trade_date"))) is not None
        and (balance := as_float(row.get("margin_purchase_today_balance"))) is not None
    )
    if len(balances) <= window:
        return None
    then = balances[-1 - window][1]
    now = balances[-1][1]
    return None if then <= 0 else (now - then) / then * 100.0


def _holder_snapshot(rows: Iterable[Mapping[str, object]]) -> tuple[float | None, int | None]:
    """(千張大戶 share %, total holders) in one weekly snapshot of one stock."""
    large = 0.0
    seen_large = False
    holders: int | None = None
    for row in rows:
        level = row.get("holding_shares_level")
        if level in LARGE_HOLDER_TIER_LABELS:
            percent = as_float(row.get("percent"))
            if percent is not None:
                large += percent
                seen_large = True
        elif level == _TOTAL_LEVEL:
            holders = as_int(row.get("people")) or None
    return (large if seen_large else None), holders


def chip_inputs(
    flow_rows: Sequence[Mapping[str, object]],
    margin_rows: Sequence[Mapping[str, object]],
    holder_snapshots: Sequence[Sequence[Mapping[str, object]]],
) -> ChipInputs:
    """One stock's 籌碼 history for the accumulation setup. `holder_snapshots` are that stock's
    weekly shareholding-distribution rows, oldest first; the comparison reaches back up to
    `HOLDER_WEEKS` snapshots (fewer when less history is stored)."""
    by_investor = _net_by_investor(flow_rows)

    def net_over_window(investor: str) -> float | None:
        per_day = by_investor.get(investor)
        if not per_day:
            return None
        return sum(net for _, net in sorted(per_day.items())[-RANGE_WINDOW:])

    foreign, trust = chip_streaks(flow_rows)
    big_now = holders_now = big_then = holders_then = None
    if len(holder_snapshots) >= 2:  # noqa: PLR2004 - a change needs two snapshots
        back = min(HOLDER_WEEKS, len(holder_snapshots) - 1)
        big_now, holders_now = _holder_snapshot(holder_snapshots[-1])
        big_then, holders_then = _holder_snapshot(holder_snapshots[-1 - back])
    return ChipInputs(
        foreign_net_20d=net_over_window(FOREIGN_INVESTOR),
        trust_net_20d=net_over_window(INVESTMENT_TRUST),
        foreign_streak=foreign,
        trust_streak=trust,
        large_holder_pct=big_now,
        large_holder_pct_before=big_then,
        holders=holders_now,
        holders_before=holders_then,
        margin_change_20d_pct=margin_change_pct(margin_rows, RANGE_WINDOW),
    )


def sector_strength(
    industry_by_symbol: Mapping[str, str], bars_by_symbol: Mapping[str, Sequence[PriceBar]]
) -> dict[str, float]:
    """Each industry's percentile (0-100) among industries by average 20-session return of its
    members. A setup in a strong sector is the more common practitioner preference."""
    returns: dict[str, list[float]] = defaultdict(list)
    for symbol, bars in bars_by_symbol.items():
        industry = industry_by_symbol.get(symbol)
        if industry is None or len(bars) <= SECTOR_RETURN_WINDOW:
            continue
        then = bars[-1 - SECTOR_RETURN_WINDOW].close
        if then > 0:
            returns[industry].append((bars[-1].close - then) / then * 100.0)
    averages = {industry: sum(values) / len(values) for industry, values in returns.items()}
    ordered = sorted(averages.values())
    if len(ordered) < 2:  # noqa: PLR2004 - a percentile needs at least two industries
        return dict.fromkeys(averages, 50.0)
    return {
        industry: sum(1 for v in ordered if v < avg) / (len(ordered) - 1) * 100.0
        for industry, avg in averages.items()
    }


def excluded_symbols(  # noqa: PLR0913 - the dates plus one argument per event feed
    *,
    run_date: date_,
    as_of: date_,
    ex_dividend_until: date_,
    attention_rows: Iterable[Mapping[str, object]],
    disposition_rows: Iterable[Mapping[str, object]],
    dividend_rows: Iterable[Mapping[str, object]],
) -> dict[str, str]:
    """Symbol -> reason for every stock the watchlist leaves out today.

    - 處置股: matching is restricted (分盤交易/預收款券), so intraday levels do not behave normally.
    - 注意股 flagged on the last session: abnormal turnover the exchange is already watching.
    - 除權息 from today through `ex_dividend_until`: the price gaps down by the dividend, which
      breaks every level computed from yesterday's close."""
    reasons: dict[str, str] = {}
    for row in disposition_rows:
        start = as_date(row.get("period_start"))
        end = as_date(row.get("period_end"))
        symbol = row.get("symbol")
        if isinstance(symbol, str) and start and end and start <= run_date <= end:
            reasons[symbol] = "disposition"
    for row in attention_rows:
        symbol = row.get("symbol")
        if isinstance(symbol, str) and as_date(row.get("flagged_date")) == as_of:
            reasons.setdefault(symbol, "attention")
    for row in dividend_rows:
        symbol = row.get("symbol")
        if not isinstance(symbol, str):
            continue
        for field in ("cash_ex_dividend_date", "stock_ex_dividend_date"):
            day = as_date(row.get(field))
            if day is not None and run_date <= day <= ex_dividend_until:
                reasons.setdefault(symbol, "ex_dividend")
    return reasons


def latest_headlines(rows: Iterable[Mapping[str, object]], since: date_) -> dict[str, str]:
    """Each symbol's newest material-announcement subject on or after `since`, shortened."""
    newest: dict[str, tuple[str, str]] = {}
    for row in rows:
        symbol = row.get("symbol")
        subject = row.get("subject")
        day = as_date(row.get("announced_date"))
        if not isinstance(symbol, str) or not isinstance(subject, str) or day is None:
            continue
        if day < since:
            continue
        stamp = f"{day.isoformat()} {row.get('announced_time') or ''}"
        if symbol not in newest or stamp > newest[symbol][0]:
            newest[symbol] = (stamp, " ".join(subject.split())[:HEADLINE_MAX_CHARS])
    return {symbol: subject for symbol, (_, subject) in newest.items()}


__all__ = [
    "as_date",
    "chip_inputs",
    "chip_streaks",
    "excluded_symbols",
    "latest_headlines",
    "margin_change_pct",
    "sector_strength",
]
