"""The morning brief's data shapes — shared by the assembling tool and the report renderer."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from schemas.chart_hint import ChartHint
from schemas.enums import Market

_NA = "N/A"
_MAX_EACH = 5


class BriefRow(BaseModel):
    """One labelled figure. `change_pct` is percent vs the prior session where the source gives
    one; `text` is the figure formatted for reading, or 「N/A」."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    text: str = _NA
    change_pct: float | None = None
    note: str = ""


class BarPoint(BaseModel):
    """One bar of a chart series: a category and its value (a percent, or 億元 for net flows)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: float


class LinePoint(BaseModel):
    """One point of a line series: `symbol` draws the line, `value` is indexed to 100 at the first
    close in the window so symbols with different prices share one axis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    date: str
    symbol: str
    value: float


class BriefEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    taipei_time: str
    et_time: str
    impact: str = ""
    consensus: str = _NA
    prior: str = _NA


class BriefHeadline(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    source: str = ""
    published_at: str = ""


class BriefEtf(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    market: Market
    close: str = _NA
    change_pct: float | None = None
    premium_pct: float | None = None
    note: str = ""


class MorningBriefInput(BaseModel):
    """The user's own symbols. Empty lists are fine — the market-level sections still fill."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    tw_stocks: tuple[str, ...] = Field(default=(), max_length=_MAX_EACH)
    us_stocks: tuple[str, ...] = Field(default=(), max_length=_MAX_EACH)
    etfs: tuple[str, ...] = Field(default=(), max_length=_MAX_EACH)


class GetMorningBriefOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    date_taipei: str
    time_taipei: str
    us_overnight: tuple[BriefRow, ...] = ()
    macro: tuple[BriefRow, ...] = ()
    tw_market: tuple[BriefRow, ...] = ()
    institutions: tuple[BriefRow, ...] = ()
    sectors_up: tuple[BriefRow, ...] = ()
    sectors_down: tuple[BriefRow, ...] = ()
    events_today: tuple[BriefEvent, ...] = ()
    watch_stocks: tuple[BriefRow, ...] = ()
    etfs: tuple[BriefEtf, ...] = ()
    us_movers: tuple[BriefRow, ...] = ()
    tw_movers: tuple[BriefRow, ...] = ()
    headlines: tuple[BriefHeadline, ...] = ()
    flags: tuple[str, ...] = ()
    unavailable: tuple[str, ...] = ()
    # Chart series (each is plotted by the `chart_hints` entry with the same `data_key`).
    us_change_series: tuple[BarPoint, ...] = ()
    sector_series: tuple[BarPoint, ...] = ()
    flow_series: tuple[BarPoint, ...] = ()
    watch_series: tuple[LinePoint, ...] = ()
    chart_hints: tuple[ChartHint, ...] = ()
    # The finished 晨間摘要 text, rendered by code from the figures above. Reproduce it verbatim.
    report: str = ""
    caveat: str = "資料為前一交易日收盤與最新公布數值，非即時報價；以下為資料整理，不構成買賣建議。"


__all__ = [
    "BarPoint",
    "BriefEtf",
    "BriefEvent",
    "BriefHeadline",
    "BriefRow",
    "GetMorningBriefOutput",
    "LinePoint",
    "MorningBriefInput",
]
