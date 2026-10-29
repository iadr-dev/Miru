"""What a 策略盯盤 notification says: the morning list and the condition-met alert, built from the
`strategy_watchlist` and `strategy_hits` rows. Pure functions, no I/O.

Copy is fact phrasing only: which stocks today's screen lists and at what level, and which of the
conditions the user subscribed to held, with the values observed. A reader whose plan does not
include the intraday (Sponsor) data gets the same alert with the intraday numbers masked.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, time
from typing import cast

from governance.textscan import check_output
from scheduler.delivery.layout import Layout, LayoutBlock, render_text
from schemas.alerts import NON_ADVICE_FOOTER
from schemas.strategy_watch import (
    CONDITION_NAME,
    STRATEGY_DEFINITION,
    STRATEGY_KEYS,
    STRATEGY_LABEL,
    phrase,
)

Row = Mapping[str, object]

MORNING_LIST_PER_STRATEGY = 5
SECTOR_LINE_LIMIT = 3
_MASKED_NOTE = "（盤中數值為 Premium 方案資料）"


def _number(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _name(row: Row | None) -> str:
    name = row.get("name") if row is not None else None
    return f" {name}" if isinstance(name, str) and name else ""


def _key_level(row: Row) -> str:
    strategy = row.get("strategy")
    if strategy == "volatility_squeeze":
        return f"上軌 {_number(row.get('upper_band'))}"
    if strategy == "oversold_bounce":
        return f"下軌 {_number(row.get('lower_band'))}"
    if strategy == "momentum_flag":
        sessions = row.get("flag_sessions")
        days = int(sessions) if isinstance(sessions, (int, float)) else 0
        return (
            f"整理 {_number(row.get('flag_low'))} 至 {_number(row.get('flag_high'))}"
            f"（{days} 日，前段 +{_number(row.get('run_gain_pct'))}%）"
        )
    if strategy == "chip_accumulation":
        return (
            f"區間 {_number(row.get('range_low_20d'))} 至 {_number(row.get('range_high_20d'))}、"
            f"大戶 {_number(row.get('large_holder_pct'))}%"
        )
    return f"20 日低點 {_number(row.get('low_20d'))}"


def _definition(key: str) -> str:
    text = STRATEGY_DEFINITION.get(key)
    return f"{STRATEGY_LABEL.get(key, key)}：{text}" if text else ""


_SECTOR_HEAD = "清單類股 20 日強度（僅就清單內股票）"


def _sector_entries(rows: Sequence[Row]) -> list[str]:
    """The industries on today's list by 20-session sector strength, with how many listed stocks
    each holds. Built from the listed rows only, so it describes the list, not the market."""
    stocks: dict[str, set[str]] = {}
    strength: dict[str, float] = {}
    for row in rows:
        industry = row.get("industry")
        if not isinstance(industry, str) or not industry:
            continue
        stocks.setdefault(industry, set()).add(str(row.get("symbol")))
        value = row.get("sector_strength_pct")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            strength[industry] = max(strength.get(industry, value), value)
    ranked = sorted(stocks, key=lambda i: (-strength.get(i, -1.0), -len(stocks[i]), i))
    return [
        f"{i} {_number(strength.get(i))}（{len(stocks[i])} 檔）" for i in ranked[:SECTOR_LINE_LIMIT]
    ]


def _sector_line(rows: Sequence[Row]) -> str:
    shown = "、".join(_sector_entries(rows))
    return f"{_SECTOR_HEAD}：{shown}" if shown else ""


def in_scope(
    rows: Iterable[Row], strategies: Sequence[str], members: frozenset[str] | None
) -> list[Row]:
    """The rows for the subscribed strategies and, for a group subscription, its members."""
    wanted = set(strategies)
    return [
        row
        for row in rows
        if row.get("strategy") in wanted and (members is None or row.get("symbol") in members)
    ]


def _market_header(rows: Sequence[Row]) -> str:
    regime = rows[0].get("regime")
    breadth = rows[0].get("breadth_pct")
    market = {"risk_on": "偏強", "neutral": "中性", "risk_off": "偏弱"}.get(str(regime))
    return f"市場寬度 {_number(breadth)}%（{market}）" if market else ""


def morning_list(rows: Sequence[Row], day: date) -> tuple[str, str] | None:
    """(title, body) for today's list, or None when nothing is on it."""
    if not rows:
        return None
    lines: list[str] = []
    for key in STRATEGY_KEYS:
        group = [r for r in rows if r.get("strategy") == key]
        if not group:
            continue
        shown = "、".join(
            f"{r.get('symbol')}{_name(r)}（{_key_level(r)}）"
            for r in group[:MORNING_LIST_PER_STRATEGY]
        )
        more = (
            f" 等 {len(group)} 檔"
            if len(group) > MORNING_LIST_PER_STRATEGY
            else f" {len(group)} 檔"
        )
        lines.append(f"{STRATEGY_LABEL[key]}{more}：{shown}")
    sector = _sector_line(rows)
    definitions = [
        _definition(key) for key in STRATEGY_KEYS if any(r.get("strategy") == key for r in rows)
    ]
    header = _market_header(rows)
    body = "\n".join(
        [
            *([header] if header else []),
            *lines,
            *([sector] if sector else []),
            *definitions,
            _MORNING_CHECK_NOTE,
        ]
    )
    return f"今日盯盤清單（{day.month}/{day.day}）", body


_MORNING_CHECK_NOTE = "盤中每 10 分鐘檢查一次，條件全數成立時通知你。"
_DEFINITIONS_HEAD = "📖 名詞"


def morning_list_layout(rows: Sequence[Row], day: date) -> Layout | None:
    """The Telegram layout of `morning_list`: the same facts, one stock per line under its
    strategy, the industries one per line, and the definitions folded away."""
    if not rows:
        return None
    header = _market_header(rows)
    blocks = [
        LayoutBlock(
            head=f"📋 今日盯盤清單（{day.month}/{day.day}）", lines=(header,) if header else ()
        )
    ]
    for key in STRATEGY_KEYS:
        group = [r for r in rows if r.get("strategy") == key]
        if not group:
            continue
        lines = [
            f"{r.get('symbol')}{_name(r)}　{_key_level(r)}"
            for r in group[:MORNING_LIST_PER_STRATEGY]
        ]
        if len(group) > MORNING_LIST_PER_STRATEGY:
            lines.append(f"…另 {len(group) - MORNING_LIST_PER_STRATEGY} 檔，見 Miru")
        blocks.append(
            LayoutBlock(head=f"{STRATEGY_LABEL[key]} · {len(group)} 檔", lines=tuple(lines))
        )
    sectors = _sector_entries(rows)
    if sectors:
        blocks.append(LayoutBlock(head=f"🏭 {_SECTOR_HEAD}", lines=tuple(sectors)))
    definitions = tuple(
        _definition(key) for key in STRATEGY_KEYS if any(r.get("strategy") == key for r in rows)
    )
    blocks.append(LayoutBlock(head=_DEFINITIONS_HEAD, lines=definitions, fold=True))
    blocks.append(LayoutBlock(lines=(_MORNING_CHECK_NOTE,)))
    return Layout(blocks=tuple(blocks))


CLOSE_SLOT = "close"


def first_alertable(
    hits: Iterable[Row], *, not_before: time | None = None, close: bool = False
) -> list[Row]:
    """One row per (symbol, strategy): the earliest intraday slot it became alertable, or with
    `close=True` the close confirmation. `not_before` drops intraday slots from before a
    subscription existed, so a new subscription does not replay the morning."""
    earliest: dict[tuple[str, str], Row] = {}
    for row in hits:
        if row.get("alertable") is not True or (row.get("slot") == CLOSE_SLOT) is not close:
            continue
        slot = str(row.get("slot", ""))
        if not close and not_before is not None and slot < not_before.strftime("%H:%M"):
            continue
        key = (str(row.get("symbol")), str(row.get("strategy")))
        if key not in earliest or slot < str(earliest[key].get("slot", "")):
            earliest[key] = row
    return sorted(earliest.values(), key=lambda r: (str(r.get("slot")), str(r.get("symbol"))))


def _conditions(row: Row) -> list[Mapping[str, object]]:
    raw = row.get("conditions")
    try:
        decoded: object = json.loads(raw) if isinstance(raw, str) else []
    except ValueError:
        return []
    if not isinstance(decoded, list):
        return []
    items = cast("list[object]", decoded)
    return [cast("Mapping[str, object]", c) for c in items if isinstance(c, dict)]


MARGIN_SURGE_PCT = 20.0


def _chip_context(watch: Row) -> list[str]:
    """The 籌碼 facts behind a 籌碼集中盤整 or 強勢整理 listing. A margin balance up more than
    `MARGIN_SURGE_PCT` in 20 sessions is stated as such: retail money chasing the move."""
    parts: list[str] = []
    change = watch.get("large_holder_change_4w")
    if isinstance(change, (int, float)) and not isinstance(change, bool):
        parts.append(
            f"千張大戶 {_number(watch.get('large_holder_pct'))}%（4 週 {change:+.2f} 個百分點）"
        )
    margin = watch.get("margin_change_20d_pct")
    if isinstance(margin, (int, float)) and not isinstance(margin, bool):
        surge = "，增幅偏大（散戶融資追價）" if margin > MARGIN_SURGE_PCT else ""
        parts.append(f"融資 20 日 {margin:+.1f}%{surge}")
    low = watch.get("range_low_20d") or watch.get("flag_low")
    if isinstance(low, (int, float)) and not isinstance(low, bool):
        label = "整理區下緣" if watch.get("strategy") == "momentum_flag" else "盤整區間下緣"
        parts.append(f"{label} {_number(low)}")
    return parts


def _context(watch: Row | None) -> str:
    if watch is None:
        return ""
    parts: list[str] = []
    if watch.get("strategy") in ("chip_accumulation", "momentum_flag"):
        parts.extend(_chip_context(watch))
    days = watch.get("days_on_list")
    if isinstance(days, int) and not isinstance(days, bool) and days > 1:
        parts.append(f"已在清單 {days} 個交易日")
    industry = watch.get("industry")
    strength = watch.get("sector_strength_pct")
    if isinstance(industry, str) and industry:
        parts.append(f"{industry} 類股強度 {_number(strength)}")
    for label, field in (("外資", "foreign_streak"), ("投信", "trust_streak")):
        streak = watch.get(field)
        if isinstance(streak, int) and not isinstance(streak, bool) and abs(streak) >= 2:  # noqa: PLR2004
            side = "買超" if streak > 0 else "賣超"
            parts.append(f"{label}連 {abs(streak)} 日{side}")
    return "背景：" + "、".join(parts) if parts else ""


def _hit_details(hit: Row, *, masked: bool) -> tuple[list[str], str]:
    """(one fact per held condition, the trailing note): the observed values, or for a masked
    reader the condition names and a note that the numbers are Premium data."""
    triggers = [c for c in _conditions(hit) if c.get("kind") == "trigger"]
    if masked:
        names = [CONDITION_NAME.get(str(c.get("key")), str(c.get("key"))) for c in triggers]
        return names, _MASKED_NOTE
    phrases = [
        phrase(
            str(c.get("key")),
            cast("float | None", c.get("observed")),
            cast("float | None", c.get("required")),
        )
        for c in triggers
    ]
    return phrases, ""


def _hit_label(hit: Row) -> str:
    return STRATEGY_LABEL.get(str(hit.get("strategy")), str(hit.get("strategy")))


def hit_alert(hit: Row, watch: Row | None, *, masked: bool) -> tuple[str, str, str]:
    """(title, body, plain_body) for one alertable hit. `plain_body` leaves out the third-party
    announcement subject, for when the output scan rejects the quoted text."""
    symbol = str(hit.get("symbol"))
    label = _hit_label(hit)
    facts, note = _hit_details(hit, masked=masked)
    detail = "、".join(facts) + note
    if hit.get("slot") == CLOSE_SLOT:
        title = f"收盤確認：{symbol}{_name(watch)} · {label}"
        lead = f"{symbol}{_name(watch)} 收盤確認你訂閱的「{label}」條件：{detail}"
    else:
        title = f"{symbol}{_name(watch)} · {label} 條件全數達成"
        lead = (
            f"{hit.get('slot')} {symbol}{_name(watch)}：你訂閱的「{label}」條件全數成立"
            f"（連續兩次檢查）：{detail}"
        )
    context = _context(watch)
    definition = _definition(str(hit.get("strategy")))
    plain = "\n".join(p for p in (lead, context, definition) if p)
    headline = watch.get("headline") if watch is not None else None
    full = f"{plain}\n今日重訊：{headline}" if isinstance(headline, str) and headline else plain
    return title, full, plain


def hit_alert_layout(
    hit: Row, watch: Row | None, *, masked: bool, with_headline: bool = True
) -> Layout:
    """The Telegram layout of `hit_alert`: the stock and strategy once as the heading, one line per
    condition, then the background, the announcement subject (when `with_headline`) and the
    folded definition."""
    close = hit.get("slot") == CLOSE_SLOT
    facts, note = _hit_details(hit, masked=masked)
    stock = f"{hit.get('symbol')}{_name(watch)} · {_hit_label(hit)}"
    heading = f"🔔 收盤確認：{stock}" if close else f"🔔 {stock}"
    when = "收盤確認你訂閱的條件" if close else f"{hit.get('slot')} 條件全數成立（連續兩次檢查）"
    lines = (when, *(f"• {f}" for f in facts), *((note,) if note else ()))
    blocks = [LayoutBlock(head=heading, lines=lines)]
    if context := _context(watch):
        blocks.append(LayoutBlock(lines=(context,)))
    headline = watch.get("headline") if watch is not None else None
    if with_headline and isinstance(headline, str) and headline:
        blocks.append(LayoutBlock(lines=(f"今日重訊：{headline}",)))
    if definition := _definition(str(hit.get("strategy"))):
        blocks.append(LayoutBlock(head=_DEFINITIONS_HEAD, lines=(definition,), fold=True))
    return Layout(blocks=tuple(blocks))


def safe_message(
    candidates: Sequence[tuple[str, Layout | None]], fallback: str
) -> tuple[str, Layout | None]:
    """The first (body, layout) pair in which both the body and the text the layout renders pass
    the output scan, so what Telegram receives is covered exactly like the plain body. Failing
    that, the first body that passes alone, without a layout. A flagged text is never sent (same
    contract as rule alerts): the last resort is a fixed line."""
    texts = [(f"{body}\n{NON_ADVICE_FOOTER}", layout) for body, layout in candidates]
    for text, layout in texts:
        if _clean(text) and (layout is None or _clean(render_text(layout, NON_ADVICE_FOOTER))):
            return text, layout
    for text, _ in texts:
        if _clean(text):
            return text, None
    return f"{fallback}\n{NON_ADVICE_FOOTER}", None


def _clean(text: str) -> bool:
    return check_output(text, has_market_data=False).reason is None


def safe_body(body: str, plain: str, fallback: str) -> str:
    """The body with the footer if it passes the output scan, else the version without quoted
    text, else a fixed line."""
    return safe_message([(body, None), (plain, None)], fallback)[0]


__all__ = [
    "MORNING_LIST_PER_STRATEGY",
    "first_alertable",
    "hit_alert",
    "hit_alert_layout",
    "in_scope",
    "morning_list",
    "morning_list_layout",
    "safe_body",
    "safe_message",
]
