"""Shared building blocks used across every FinMind dataset-category module.

Coercion helpers for FinMind's dynamically-shaped upstream JSON, the per-dataset config
record every category module fills in, the family of "resolve which `Symbol` a row belongs
to" helpers (each dataset family keys its whole-market rows under a different column name),
and the `SourceResult` envelope constructor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from schemas.enums import DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import SourceHealth, SourceResult


def _as_float(value: object, default: float = 0.0) -> float:
    """Coerce an untyped upstream JSON scalar to float. Isolated 3rd-party edge — upstream
    payloads are dynamically shaped `object` values."""
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _as_int(value: object, default: int = 0) -> int:
    """Coerce an untyped upstream JSON scalar to int, via `_as_float`."""
    return int(_as_float(value, float(default)))


@dataclass(frozen=True)
class _DatasetConfig:
    dataset: str
    data_class: DataClass
    timing_class: TimingClass
    caveat: str
    row_key: str  # key under which normalized rows are stored in SourceResult.payload
    row_mapper: Callable[[list[dict[str, object]], Symbol | None], list[dict[str, object]]]
    date_param: str = "start_date"  # some endpoints (chip_broker) use "date" instead
    special_endpoint: str | None = None  # None => the generic /data endpoint
    whole_market_ok: bool = False  # this dataset documents an omit-data_id whole-market form
    market: Market = Market.TW  # the market this capability's dataset actually covers; only
    # the International Markets + Global Economic Data datasets from the third coverage pass
    # override this away from the TW default.


def _row_symbol(row: dict[str, object], sym: Symbol | None) -> Symbol:
    """Resolve the `Symbol` a row belongs to.

    Per-symbol calls pass a fixed `sym`. Whole-market calls (`fetch_whole_market`, the
    ingest-only whole-market-by-date carve-out) pass `sym=None` — the market-wide response has
    one row per stock, so the symbol must be read off each row's own `stock_id` rather than a
    caller-supplied one.
    """
    if sym is not None:
        return sym
    return Symbol(id=str(row.get("stock_id", "")), market=Market.TW)


def _row_derivative_symbol(row: dict[str, object], sym: Symbol | None, id_field: str) -> Symbol:
    """Like `_row_symbol` but for derivative datasets whose whole-market row carries the
    contract id under `futures_id`/`option_id` rather than `stock_id`."""
    if sym is not None:
        return sym
    return Symbol(id=str(row.get(id_field, "")), market=Market.TW)


def _row_symbol_field(
    row: dict[str, object], sym: Symbol | None, id_field: str, market: Market
) -> Symbol:
    """Like `_row_symbol` but for datasets whose whole-market row carries the id under an
    arbitrary column name (and/or belongs to a non-TW market)."""
    if sym is not None:
        return sym
    return Symbol(id=str(row.get(id_field, "")), market=market)


def _row_cb_symbol(row: dict[str, object], sym: Symbol | None) -> Symbol:
    """Convertible-bond datasets key on `cb_id`, a convertible-bond identifier — never an
    equity `stock_id`."""
    if sym is not None:
        return sym
    return Symbol(id=str(row.get("cb_id", "")), market=Market.TW)


def _result(
    *,
    data_class: DataClass,
    timing_class: TimingClass,
    caveat: str,
    payload: dict[str, object],
    health: SourceHealth,
) -> SourceResult:
    """Build the `SourceResult` envelope common to every FinMind fetch outcome."""
    return SourceResult(
        source_id="FINMIND",
        data_class=data_class,
        timing_class=timing_class,
        fetched_at=datetime.now(UTC),
        currentness_caveat=caveat,
        payload=payload,
        health=health,
    )


__all__ = [
    "_DatasetConfig",
    "_as_float",
    "_as_int",
    "_result",
    "_row_cb_symbol",
    "_row_derivative_symbol",
    "_row_symbol",
    "_row_symbol_field",
]
