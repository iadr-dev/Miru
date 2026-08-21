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


def _as_date_str(value: object) -> str:
    """Coerce a FinMind `date` column to a bare `YYYY-MM-DD` string. Confirmed empirically:
    `TaiwanStockNews` returns a full `YYYY-MM-DD HH:MM:SS` timestamp (e.g.
    `2026-07-18 01:00:59`) rather than a plain date, which pydantic's `date` field rejects
    outright (`date_from_datetime_inexact`) — every other FinMind dataset's `date` column is
    already bare, so this truncation is isolated to the one row mapper that needs it rather
    than applied blindly to every `trade_date`/`date` assignment."""
    text = str(value)
    return text.split(" ", 1)[0]


def _as_optional_str(value: object) -> str | None:
    """Coerce an untyped upstream JSON scalar to `str | None`, treating an empty string the same
    as absent. Confirmed empirically: `SecuritiesLending`'s `original_lending_period` sometimes
    returns a bare int (e.g. `184`, a day count) rather than a string, which pydantic's `str |
    None` field rejects outright (`string_type`) — a plain `row.get(key) or None` passes that int
    straight through instead of coercing it."""
    if value is None or value == "":
        return None
    return str(value)


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
    no_end_date_param: bool = False  # this dataset's upstream endpoint 400s if `end_date` is
    # sent at all (confirmed empirically: TaiwanStockIndustryChainMoneyFlow — "size too large,
    # we only send one day data" — and TaiwanStockBlockTradingDailyReport, both single-trading-
    # day-per-request datasets whose own doc explicitly says "do NOT pass end_date"). The
    # adapter's own default-range fill-in must not silently attach one just because a caller
    # omitted `start_date`.
    no_start_date_param: bool = False  # this dataset's upstream endpoint silently returns ZERO
    # rows if `start_date` is present at all (confirmed empirically:
    # TaiwanStockConvertibleBondInfo, a static whole-roster reference table with no date
    # semantics — the doc's own Params line lists no date param, and passing one doesn't 400,
    # it just filters the response down to nothing). Distinct from the `no_end_date_param`/
    # `no_data_id_param` 400-on-presence cases below — this one fails silently, which is worse:
    # a caller that always includes a default start_date (the whole point of this default-range
    # mechanism) would otherwise never see a row from this dataset.
    no_data_id_param: bool = False  # this dataset's upstream endpoint 400s if `data_id` is sent
    # at all (confirmed empirically: TaiwanTotalExchangeMarginMaintenance/
    # TaiwanStockParValueChange reject it even though the generic per-symbol `fetch()` path
    # would otherwise always include one) — genuinely whole-market-only, never data_id-filterable,
    # distinct from `whole_market_ok` (which describes an *additional* omit-data_id form some
    # per-symbol-capable datasets also support). `fetch()` omits `data_id` when this is set and
    # relies on the row_mapper's own `sym` parameter to attribute rows to the caller's symbol.
    default_lookback_days: int = 30  # how far back an omitted `start_date` defaults to — must
    # cover at least one real occurrence of this dataset's own publish cadence, or a caller that
    # never names a date always gets zero rows back. A flat 30-day default (fine for genuinely
    # daily datasets like price/margin/institutional-flow) silently starves anything published
    # less often than monthly — confirmed empirically: FINANCIALS/BALANCE_SHEET/CASH_FLOW/
    # DIVIDENDS/etc. returned 0 rows at 30 days and hundreds of rows at 450+. Each category
    # module sets this explicitly for its own non-daily datasets rather than leaving the
    # daily-tuned global default to silently under-serve them.


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
    "_as_date_str",
    "_as_float",
    "_as_int",
    "_as_optional_str",
    "_result",
    "_row_cb_symbol",
    "_row_derivative_symbol",
    "_row_symbol",
    "_row_symbol_field",
]
