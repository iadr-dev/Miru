"""Characterization of `_build_query`: which of `dataset`/`data_id`/`date`/`start_date`/
`end_date` reach the FinMind `/data` query for each `_DatasetConfig` flag combination, with
and without caller-supplied dates. The clock is pinned; no network."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

import sources.finmind._adapter as adapter_module
from schemas.enums import DataClass, TimingClass
from sources.finmind._adapter import _build_query
from sources.finmind._shared import _DatasetConfig

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> _FrozenDatetime:  # type: ignore[override]
        return cls(NOW.year, NOW.month, NOW.day, NOW.hour, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(adapter_module, "datetime", _FrozenDatetime)


def _config(**overrides: object) -> _DatasetConfig:
    base: dict[str, object] = {
        "dataset": "TaiwanStockX",
        "data_class": DataClass.PUBLIC,
        "timing_class": TimingClass.OFFICIAL_EOD,
        "caveat": "c",
        "row_key": "rows",
        "row_mapper": lambda rows, sym: rows,
    }
    base.update(overrides)
    return _DatasetConfig(**base)  # type: ignore[arg-type]


def test_explicit_range_passes_through_untouched() -> None:
    query = _build_query(
        _config(),
        data_id="2330",
        params={"start_date": "2026-01-01", "end_date": "2026-02-01"},
    )

    assert query == {
        "dataset": "TaiwanStockX",
        "data_id": "2330",
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
    }


def test_data_id_none_is_omitted_and_dates_are_stringified() -> None:
    query = _build_query(
        _config(),
        data_id=None,
        params={"start_date": date(2026, 1, 1), "end_date": date(2026, 2, 1)},
    )

    assert query == {
        "dataset": "TaiwanStockX",
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
    }


def test_omitted_start_defaults_to_the_lookback_and_fills_the_end_with_today() -> None:
    query = _build_query(_config(default_lookback_days=450), data_id=None, params={})

    assert query == {
        "dataset": "TaiwanStockX",
        "start_date": "2025-07-27",
        "end_date": "2026-10-20",
    }


def test_omitted_start_keeps_a_caller_supplied_end_date() -> None:
    query = _build_query(_config(), data_id=None, params={"end_date": "2026-09-01"})

    assert query["start_date"] == "2026-09-20"
    assert query["end_date"] == "2026-09-01"


def test_omitted_start_on_a_no_end_date_dataset_never_attaches_an_end() -> None:
    query = _build_query(_config(no_end_date_param=True), data_id=None, params={})

    assert query == {"dataset": "TaiwanStockX", "start_date": "2026-09-20"}


def test_supplied_end_is_dropped_for_a_no_end_date_dataset() -> None:
    query = _build_query(
        _config(no_end_date_param=True),
        data_id=None,
        params={"start_date": "2026-01-01", "end_date": "2026-02-01"},
    )

    assert query == {"dataset": "TaiwanStockX", "start_date": "2026-01-01"}


def test_no_start_date_dataset_returns_only_dataset_and_data_id_whatever_is_passed() -> None:
    query = _build_query(
        _config(no_start_date_param=True),
        data_id="123",
        params={"start_date": "2026-01-01", "end_date": "2026-02-01", "date": "2026-01-05"},
    )

    assert query == {"dataset": "TaiwanStockX", "data_id": "123"}


def test_single_day_dataset_prefers_the_date_param_over_start_date() -> None:
    query = _build_query(
        _config(date_param="date"),
        data_id="9",
        params={"date": "2026-03-03", "start_date": "2026-01-01", "end_date": "2026-02-01"},
    )

    assert query == {"dataset": "TaiwanStockX", "data_id": "9", "date": "2026-03-03"}


def test_single_day_dataset_falls_back_to_start_date_then_to_yesterday() -> None:
    config = _config(date_param="date")

    from_start = _build_query(config, data_id=None, params={"start_date": "2026-01-01"})
    default = _build_query(config, data_id=None, params={})

    assert from_start == {"dataset": "TaiwanStockX", "date": "2026-01-01"}
    assert default == {"dataset": "TaiwanStockX", "date": "2026-10-19"}
