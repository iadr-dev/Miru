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
from schemas.alerts import NON_ADVICE_FOOTER
from schemas.strategy_watch import CONDITION_NAME, STRATEGY_KEYS, STRATEGY_LABEL, phrase

Row = Mapping[str, object]

MORNING_LIST_PER_STRATEGY = 5
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
    return f"20 日低點 {_number(row.get('low_20d'))}"


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
    regime = rows[0].get("regime")
    breadth = rows[0].get("breadth_pct")
    market = {"risk_on": "偏強", "neutral": "中性", "risk_off": "偏弱"}.get(str(regime))
    header = f"市場寬度 {_number(breadth)}%（{market}）" if market else ""
    body = "\n".join(
        [*([header] if header else []), *lines, "盤中每 10 分鐘檢查一次，條件全數成立時通知你。"]
    )
    return f"今日盯盤清單（{day.month}/{day.day}）", body


def first_alertable(hits: Iterable[Row], *, not_before: time | None = None) -> list[Row]:
    """One row per (symbol, strategy): the earliest slot it became alertable. `not_before` drops
    slots from before a subscription existed, so a new subscription does not replay the morning."""
    earliest: dict[tuple[str, str], Row] = {}
    for row in hits:
        if row.get("alertable") is not True:
            continue
        slot = str(row.get("slot", ""))
        if not_before is not None and slot < not_before.strftime("%H:%M"):
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


def _context(watch: Row | None) -> str:
    if watch is None:
        return ""
    parts: list[str] = []
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


def hit_alert(hit: Row, watch: Row | None, *, masked: bool) -> tuple[str, str, str]:
    """(title, body, plain_body) for one alertable hit. `plain_body` leaves out the third-party
    announcement subject, for when the output scan rejects the quoted text."""
    symbol = str(hit.get("symbol"))
    label = STRATEGY_LABEL.get(str(hit.get("strategy")), str(hit.get("strategy")))
    triggers = [c for c in _conditions(hit) if c.get("kind") == "trigger"]
    if masked:
        facts = "、".join(
            CONDITION_NAME.get(str(c.get("key")), str(c.get("key"))) for c in triggers
        )
        detail = f"{facts}{_MASKED_NOTE}"
    else:
        phrases = [
            phrase(
                str(c.get("key")),
                cast("float | None", c.get("observed")),
                cast("float | None", c.get("required")),
            )
            for c in triggers
        ]
        detail = "、".join(phrases)
    title = f"{symbol}{_name(watch)} · {label} 條件全數達成"
    lead = (
        f"{hit.get('slot')} {symbol}{_name(watch)}：你訂閱的「{label}」條件全數成立"
        f"（連續兩次檢查）：{detail}"
    )
    context = _context(watch)
    plain = "\n".join(p for p in (lead, context) if p)
    headline = watch.get("headline") if watch is not None else None
    full = f"{plain}\n今日重訊：{headline}" if isinstance(headline, str) and headline else plain
    return title, full, plain


def safe_body(body: str, plain: str, fallback: str) -> str:
    """The body with the footer if it passes the output scan, else the version without quoted
    text, else a fixed line: a flagged text is never sent (same contract as rule alerts)."""
    for candidate in (body, plain):
        text = f"{candidate}\n{NON_ADVICE_FOOTER}"
        if check_output(text, has_market_data=False).reason is None:
            return text
    return f"{fallback}\n{NON_ADVICE_FOOTER}"


__all__ = [
    "MORNING_LIST_PER_STRATEGY",
    "first_alertable",
    "hit_alert",
    "in_scope",
    "morning_list",
    "safe_body",
]
