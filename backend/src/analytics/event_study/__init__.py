"""Descriptive event study: what stocks historically did over the next N trading days after a
dated event. Pure statistics over stored data; nothing here predicts or recommends."""

from __future__ import annotations

from analytics.event_study.events import (
    EventDateFilter,
    attention_events,
    buyback_events,
    capital_reduction_events,
    disposition_events,
)
from analytics.event_study.loader import (
    EVENT_DATASET,
    EventCoverage,
    load_close_panel,
    load_snapshot_events,
    price_calendar,
)
from analytics.event_study.models import (
    DEFAULT_HORIZONS,
    MIN_SAMPLE_SIZE,
    PRE_EVENT_DAYS,
    PROXY_ID,
    ClosePanel,
    EventOutcome,
    EventType,
    ExtractionResult,
    HorizonSummary,
    OutcomeCounts,
    SampleSummary,
    StudyEvent,
    StudyResult,
    YearSummary,
)
from analytics.event_study.study import run_event_study, summarize_sample

__all__ = [
    "DEFAULT_HORIZONS",
    "EVENT_DATASET",
    "MIN_SAMPLE_SIZE",
    "PRE_EVENT_DAYS",
    "PROXY_ID",
    "ClosePanel",
    "EventCoverage",
    "EventDateFilter",
    "EventOutcome",
    "EventType",
    "ExtractionResult",
    "HorizonSummary",
    "OutcomeCounts",
    "SampleSummary",
    "StudyEvent",
    "StudyResult",
    "YearSummary",
    "attention_events",
    "buyback_events",
    "capital_reduction_events",
    "disposition_events",
    "load_close_panel",
    "load_snapshot_events",
    "price_calendar",
    "run_event_study",
    "summarize_sample",
]
