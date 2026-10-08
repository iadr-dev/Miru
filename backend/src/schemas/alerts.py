"""Alert-rule vocabulary shared across layers — the typed `condition {kind, ...}` union, the
trigger modes, and the fact-phrasing of a condition (docs/backend/modules/14 §3).

Lives in `schemas/` (the lowest layer) because both the scheduler engine *and* the agent's
`propose_alert` tool need the exact same definitions: a proposal the tool accepts must be a rule
`POST /alerts` accepts. Nothing here touches I/O or any dataset — the parts that map a template to
an ingested dataset / capability stay in `scheduler.templates`, which re-exports this module so
existing imports keep working.

Each template names what it watches as a user-set, data-observable *fact*. Thresholds are
decimals. Copy is fact-style ("a condition you set was met"): every phrase states what the data
shows against the user's own threshold and never tells anyone to act (governance §1 / modules/14
§F). `NON_ADVICE_FOOTER` rides on every notification body.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import Market

NON_ADVICE_FOOTER: Final = "本通知為你設定條件的資料提醒，非投資建議。"

# How many prior trading days a history-dependent template reads (bounded — never a scan).
MAX_HISTORY_DAYS: Final = 60

_INVESTOR_LABEL: Final = {"foreign": "外資", "trust": "投信", "dealer": "自營商"}


class TriggerMode(StrEnum):
    """How often a rule may fire: once then auto-disable, at most once a day, or on every
    occurrence (still bounded by cooldown, the daily cap, and the spam auto-pause)."""

    ONCE = "once"
    ONCE_PER_DAY = "once_per_day"
    EVERY_OCCURRENCE = "every_occurrence"


class _Template(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ClosePrice(_Template):
    """Closing price is above/below a level the user chose."""

    kind: Literal["close_price"] = "close_price"
    comparator: Literal["above", "below"]
    value: float = Field(gt=0)


class DailyChange(_Template):
    """Single-day move versus the prior trading day's close reaches a percentage."""

    kind: Literal["daily_change"] = "daily_change"
    direction: Literal["up", "down"]
    percent: float = Field(gt=0, le=100)


class VolumeSurge(_Template):
    """Volume is at least `multiple` times its trailing `window`-day average."""

    kind: Literal["volume_surge"] = "volume_surge"
    multiple: float = Field(gt=1, le=100)
    window: int = Field(default=20, ge=5, le=MAX_HISTORY_DAYS)


class InstitutionalStreak(_Template):
    """An institutional investor class net bought/sold for `days` consecutive trading days."""

    kind: Literal["institutional_streak"] = "institutional_streak"
    investor: Literal["foreign", "trust", "dealer"]
    direction: Literal["buy", "sell"]
    days: int = Field(ge=2, le=20)


class AttentionListEntry(_Template):
    """The symbol appears on the exchange 注意股 list."""

    kind: Literal["attention_entry"] = "attention_entry"


class DispositionListEntry(_Template):
    """The symbol appears on the exchange 處置股 (disposition) list."""

    kind: Literal["disposition_entry"] = "disposition_entry"


class MarginBalanceChange(_Template):
    """融資 (margin purchase) or 融券 (short sale) balance moved by a percentage in one day,
    computed from the row's own today-vs-yesterday balances."""

    kind: Literal["margin_balance_change"] = "margin_balance_change"
    side: Literal["margin", "short"]
    direction: Literal["up", "down"]
    percent: float = Field(gt=0, le=1000)


class EtfPremiumDiscount(_Template):
    """An ETF's 預估折溢價 (estimated premium/discount to NAV, TWSE's own published figure) is at
    least `percent` on the chosen side. Applies to ETF codes only — a stock has no NAV row."""

    kind: Literal["etf_premium_discount"] = "etf_premium_discount"
    direction: Literal["premium", "discount"]
    percent: float = Field(gt=0, le=50)


class MonthRevenuePublished(_Template):
    """The symbol's monthly-revenue (月營收) figure has been published."""

    kind: Literal["month_revenue_published"] = "month_revenue_published"


class MaterialAnnouncementPublished(_Template):
    """The company filed a 重大訊息 (material announcement) with the exchange. An event, not a
    state: it says a filing exists - the notification quotes the filer's own subject line as data,
    and never characterises it."""

    kind: Literal["material_announcement"] = "material_announcement"


RuleCondition = Annotated[
    ClosePrice
    | DailyChange
    | VolumeSurge
    | InstitutionalStreak
    | AttentionListEntry
    | DispositionListEntry
    | MonthRevenuePublished
    | MaterialAnnouncementPublished
    | MarginBalanceChange
    | EtfPremiumDiscount,
    Field(discriminator="kind"),
]

ConditionModel = (
    ClosePrice
    | DailyChange
    | VolumeSurge
    | InstitutionalStreak
    | AttentionListEntry
    | DispositionListEntry
    | MonthRevenuePublished
    | MaterialAnnouncementPublished
    | MarginBalanceChange
    | EtfPremiumDiscount
)

# Templates backed by Taiwan-only data (三大法人, exchange watch lists, 月營收). A rule pairing one
# of these with a US symbol could never fire, so creation refuses it instead of accepting a dead
# rule the user would wait on forever.
TW_ONLY_KINDS: Final = frozenset(
    {
        "institutional_streak",
        "attention_entry",
        "disposition_entry",
        "month_revenue_published",
        "material_announcement",
        "margin_balance_change",
        "etf_premium_discount",
    }
)

# Templates that report a discrete event rather than a state that stays true. A state template
# fires on its false->true transition; an event is a new occurrence every day it happens, so
# yesterday's filing must not silence today's.
EVENT_KINDS: Final = frozenset({"material_announcement"})


# Templates whose threshold is a value in the stock's own units (a price). They mean something only
# for one specific stock, so a watchlist-group rule refuses them and they are set per stock. Every
# other template is scale-free (a percent, a multiple, a day count, a list membership) and reads the
# same for every member of a group.
PER_STOCK_KINDS: Final = frozenset({"close_price"})


def supports_market(condition: ConditionModel, market: Market) -> bool:
    """Whether `condition` has data for `market` (price-derived templates exist for TW and US)."""
    return market is Market.TW or condition.kind not in TW_ONLY_KINDS


# Templates with no parameters are a single fixed fact.
_FLAG_PHRASE: Final = {
    "attention_entry": "列入注意股名單",
    "disposition_entry": "列入處置股名單",
    "month_revenue_published": "公布月營收",
    "material_announcement": "公布重大訊息",
}


def describe(condition: ConditionModel) -> str:
    """The rule in plain fact phrasing — the UI preview line and the notification subject."""
    if isinstance(condition, ClosePrice):
        relation = "高於" if condition.comparator == "above" else "低於"
        phrase = f"收盤價{relation} {condition.value:g}"
    elif isinstance(condition, DailyChange):
        move = "漲幅" if condition.direction == "up" else "跌幅"
        phrase = f"單日{move}達 {condition.percent:g}%"
    elif isinstance(condition, VolumeSurge):
        phrase = f"成交量達近 {condition.window} 日均量的 {condition.multiple:g} 倍"
    elif isinstance(condition, InstitutionalStreak):
        side = "淨買超" if condition.direction == "buy" else "淨賣超"
        phrase = f"{_INVESTOR_LABEL[condition.investor]}連續 {condition.days} 日{side}"
    elif isinstance(condition, MarginBalanceChange):
        phrase = _margin_phrase(condition)
    elif isinstance(condition, EtfPremiumDiscount):
        side = "溢價" if condition.direction == "premium" else "折價"
        phrase = f"預估{side}幅度達 {condition.percent:g}%"
    else:
        phrase = _FLAG_PHRASE[condition.kind]
    return phrase


def _margin_phrase(condition: MarginBalanceChange) -> str:
    label = "融資" if condition.side == "margin" else "融券"
    move = "增加" if condition.direction == "up" else "減少"
    return f"{label}餘額單日{move}達 {condition.percent:g}%"


def investor_label(investor: str) -> str:
    return _INVESTOR_LABEL[investor]


__all__ = [
    "EVENT_KINDS",
    "MAX_HISTORY_DAYS",
    "NON_ADVICE_FOOTER",
    "PER_STOCK_KINDS",
    "TW_ONLY_KINDS",
    "AttentionListEntry",
    "ClosePrice",
    "ConditionModel",
    "DailyChange",
    "DispositionListEntry",
    "EtfPremiumDiscount",
    "InstitutionalStreak",
    "MarginBalanceChange",
    "MaterialAnnouncementPublished",
    "MonthRevenuePublished",
    "RuleCondition",
    "TriggerMode",
    "VolumeSurge",
    "describe",
    "investor_label",
    "supports_market",
]
