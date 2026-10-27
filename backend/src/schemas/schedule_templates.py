"""The scheduled-briefing catalogue (docs/backend/modules/14 §4) — one source for the chat agent's
`propose_schedule` tool and anything else that needs to turn "a daily pre-open brief for 2330"
into a concrete trigger + skill + inputs.

Each template names a governed skill (`skills/built_ins/`), when it runs, and which skill input
carries the user's target(s). Skills disagree on that input's name and arity (`stock_ids` is a
list, `stock_id` / `etf_id` / `symbol_id` are single), which is why it lives here instead of in
every caller. This sits in `schemas/` so both `tools/` and the API can import it without a layering
breach; the scheduler itself validates the resulting trigger/skill as it does for any schedule.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from schemas.enums import Market, UserMarket


class ScheduleTemplateId(StrEnum):
    PRE_OPEN_BRIEF = "pre_open_brief"
    AFTER_CLOSE_CHIP = "after_close_chip"
    DISPOSITION_WATCH = "disposition_watch"
    WEEKLY_CATALYST = "weekly_catalyst"
    TW_STOCK_REPORT = "tw_stock_report"
    US_STOCK_REPORT = "us_stock_report"
    TW_ETF_REPORT = "tw_etf_report"
    US_ETF_REPORT = "us_etf_report"
    WEEKLY_WATCHLIST_REVIEW = "weekly_watchlist_review"


@dataclass(frozen=True)
class ScheduleTemplate:
    id: ScheduleTemplateId
    skill_ref: str
    trigger_type: str  # "market_event" | "cron"
    trigger_spec: str
    market: UserMarket
    # The skill input that carries the user's target(s), and whether it takes a list.
    target_input: str
    multiple: bool
    # False for a briefing about the user's whole account rather than named stocks (the weekly
    # watchlist review): it takes no ticker `targets`, only its fixed `extra_inputs`.
    takes_targets: bool = True
    extra_inputs: Mapping[str, object] = MappingProxyType({})
    # Plain-language cadence, for the agent and the proposal card.
    cadence: str = ""


MAX_TARGETS: Final = 10

# A schedule may scope itself to one of the owner's watchlists instead of listing stocks: the input
# holds the watchlist id and the worker expands it to the list's current members on every run.
# Only the multi-target TW briefing takes a list of stocks, so only it supports this.
WATCHLIST_INPUT: Final = "watchlist_id"
WATCHLIST_SKILL: Final = "daily-context-brief"

# The weekly watchlist review is the personal counterpart of the market-wide morning brief: it runs
# the existing `watchlist-review` skill in its weekly mode over ALL of the owner's watchlist groups,
# so there are no tickers to name. The skill input below is how a run (and the worker's "nothing to
# review" check) recognises it; the day and time are the user's, the default is Saturday 09:00
# Taipei (after Friday's close in both Taipei and New York).
WEEKLY_REVIEW_SKILL: Final = "watchlist-review"
WEEKLY_REVIEW_INPUT: Final = "review"
WEEKLY_REVIEW_MODE: Final = "weekly"
_SATURDAY_0900: Final = "0 9 * * 6"
WEEKLY_REVIEW_DEFAULT_CRON: Final = _SATURDAY_0900
WEEKDAY_CRON: Final[dict[str, int]] = {
    "sun": 0,
    "mon": 1,
    "tue": 2,
    "wed": 3,
    "thu": 4,
    "fri": 5,
    "sat": 6,
}
WEEKDAY_NAMES_ZH: Final[dict[int, str]] = {
    0: "日",
    1: "一",
    2: "二",
    3: "三",
    4: "四",
    5: "五",
    6: "六",
}


def is_weekly_review(skill_ref: str, inputs: Mapping[str, object]) -> bool:
    """Whether a schedule action is the weekly watchlist review."""
    return skill_ref == WEEKLY_REVIEW_SKILL and inputs.get(WEEKLY_REVIEW_INPUT) == (
        WEEKLY_REVIEW_MODE
    )


def weekly_review_cron(weekday: str | None, hour: int, minute: int) -> str:
    """The cron spec for the review on `weekday` (mon..sun; None = Saturday) at `hour:minute`."""
    day = WEEKDAY_CRON[weekday] if weekday is not None else 6
    return f"{minute} {hour} * * {day}"


SCHEDULE_TEMPLATES: Final[dict[ScheduleTemplateId, ScheduleTemplate]] = {
    ScheduleTemplateId.PRE_OPEN_BRIEF: ScheduleTemplate(
        id=ScheduleTemplateId.PRE_OPEN_BRIEF,
        skill_ref="daily-context-brief",
        trigger_type="market_event",
        trigger_spec="pre_open",
        market=Market.TW,
        target_input="stock_ids",
        multiple=True,
        cadence="每個台股交易日開盤前",
    ),
    ScheduleTemplateId.AFTER_CLOSE_CHIP: ScheduleTemplate(
        id=ScheduleTemplateId.AFTER_CLOSE_CHIP,
        skill_ref="chip-flow-review",
        trigger_type="market_event",
        trigger_spec="after_close",
        market=Market.TW,
        target_input="stock_id",
        multiple=False,
        cadence="每個台股交易日收盤後",
    ),
    ScheduleTemplateId.DISPOSITION_WATCH: ScheduleTemplate(
        id=ScheduleTemplateId.DISPOSITION_WATCH,
        skill_ref="disposition-watch",
        trigger_type="market_event",
        trigger_spec="after_close",
        market=Market.TW,
        target_input="stock_id",
        multiple=False,
        cadence="每個台股交易日收盤後",
    ),
    ScheduleTemplateId.WEEKLY_CATALYST: ScheduleTemplate(
        id=ScheduleTemplateId.WEEKLY_CATALYST,
        skill_ref="catalyst-scan",
        trigger_type="cron",
        trigger_spec="30 8 * * 1",
        market=Market.TW,
        target_input="stock_id",
        multiple=False,
        cadence="每週一 08:30",
    ),
    # The full analysis reports (5-year table, charts, seasonality, risk). Weekly on purpose: each
    # run makes a dozen data calls and counts against the chat quota, and a five-year report does
    # not change daily.
    ScheduleTemplateId.TW_STOCK_REPORT: ScheduleTemplate(
        id=ScheduleTemplateId.TW_STOCK_REPORT,
        skill_ref="stock-report-tw",
        trigger_type="cron",
        trigger_spec="30 8 * * 1",
        market=Market.TW,
        target_input="stock_id",
        multiple=False,
        extra_inputs=MappingProxyType({"market": "TW"}),
        cadence="每週一 08:30",
    ),
    ScheduleTemplateId.US_STOCK_REPORT: ScheduleTemplate(
        id=ScheduleTemplateId.US_STOCK_REPORT,
        skill_ref="stock-report-us",
        trigger_type="cron",
        trigger_spec=_SATURDAY_0900,
        market=Market.US,
        target_input="symbol_id",
        multiple=False,
        extra_inputs=MappingProxyType({"market": "US"}),
        cadence="每週六（美東上午 9:00，週五收盤後）",
    ),
    ScheduleTemplateId.TW_ETF_REPORT: ScheduleTemplate(
        id=ScheduleTemplateId.TW_ETF_REPORT,
        skill_ref="etf-report",
        trigger_type="cron",
        trigger_spec="30 15 * * 5",
        market=Market.TW,
        target_input="etf_id",
        multiple=False,
        extra_inputs=MappingProxyType({"market": "TW"}),
        cadence="每週五 15:30",
    ),
    ScheduleTemplateId.US_ETF_REPORT: ScheduleTemplate(
        id=ScheduleTemplateId.US_ETF_REPORT,
        skill_ref="etf-report",
        trigger_type="cron",
        trigger_spec=_SATURDAY_0900,
        market=Market.US,
        target_input="etf_id",
        multiple=False,
        extra_inputs=MappingProxyType({"market": "US"}),
        cadence="每週六（美東上午 9:00，週五收盤後）",
    ),
    ScheduleTemplateId.WEEKLY_WATCHLIST_REVIEW: ScheduleTemplate(
        id=ScheduleTemplateId.WEEKLY_WATCHLIST_REVIEW,
        skill_ref=WEEKLY_REVIEW_SKILL,
        trigger_type="cron",
        trigger_spec=WEEKLY_REVIEW_DEFAULT_CRON,
        market=Market.TW,
        target_input="",
        multiple=False,
        takes_targets=False,
        extra_inputs=MappingProxyType({WEEKLY_REVIEW_INPUT: WEEKLY_REVIEW_MODE}),
        cadence="每週六 09:00",
    ),
}


def build_inputs(template: ScheduleTemplate, targets: list[str]) -> dict[str, object]:
    """The skill `inputs` for these targets: a list for a multi-target skill, the one id otherwise,
    plus any fixed inputs the skill needs."""
    inputs: dict[str, object] = dict(template.extra_inputs)
    if not template.takes_targets:
        return inputs
    inputs[template.target_input] = list(targets) if template.multiple else targets[0]
    return inputs


__all__ = [
    "MAX_TARGETS",
    "SCHEDULE_TEMPLATES",
    "WEEKDAY_CRON",
    "WEEKDAY_NAMES_ZH",
    "WEEKLY_REVIEW_DEFAULT_CRON",
    "WEEKLY_REVIEW_INPUT",
    "WEEKLY_REVIEW_MODE",
    "WEEKLY_REVIEW_SKILL",
    "ScheduleTemplate",
    "ScheduleTemplateId",
    "build_inputs",
    "is_weekly_review",
    "weekly_review_cron",
]
