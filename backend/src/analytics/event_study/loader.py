"""Read the stored event feeds and the price window for the event study (read-only).

The attention-stock, disposition and material-announcement feeds are daily snapshots. Their live
history starts the day the daily job first ran; for attention and disposition the earlier days
are rebuilt from the exchanges' range reports (`ingest/event_history.py`) into the same
partitions, so this loader reads both alike. `EventCoverage` reports exactly which snapshots
were read so the output can say how far back they go.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date as date_
from typing import Final, cast

import numpy as np
import numpy.typing as npt
import pandas as pd

from analytics.event_study.events import (
    EventDateFilter,
    attention_events,
    buyback_events,
    disposition_events,
)
from analytics.event_study.models import ClosePanel, EventType, ExtractionResult
from analytics.symbol_universe import scoreable_symbol_sql
from ingest.rows import rows_from_store
from ingest.store import ParquetStore
from schemas.capabilities import (
    AttentionStockRow,
    DispositionEventRow,
    MaterialAnnouncementRow,
)
from schemas.enums import Market

PRICE_EOD_DATASET: Final[str] = "TaiwanStockPrice"
ATTENTION_STOCKS_DATASET: Final[str] = "attention_stocks"
DISPOSITION_EVENTS_DATASET: Final[str] = "disposition_events"
MATERIAL_ANNOUNCEMENTS_DATASET: Final[str] = "material_announcements"
_MARKET: Final[str] = Market.TW.value

# Snapshot-fed event types and the stored dataset each one reads.
EVENT_DATASET: Final[dict[EventType, str]] = {
    EventType.DISPOSITION_START: DISPOSITION_EVENTS_DATASET,
    EventType.DISPOSITION_END: DISPOSITION_EVENTS_DATASET,
    EventType.ATTENTION_LISTED: ATTENTION_STOCKS_DATASET,
    EventType.BUYBACK_START: MATERIAL_ANNOUNCEMENTS_DATASET,
    EventType.BUYBACK_END: MATERIAL_ANNOUNCEMENTS_DATASET,
}

_PRICE_COLUMNS: Final[tuple[str, ...]] = ("symbol", "trade_date", "close", "volume")


@dataclass(frozen=True, slots=True)
class EventCoverage:
    """The stored snapshots an event type was built from."""

    dataset: str
    first_snapshot: date_ | None
    last_snapshot: date_ | None
    n_snapshots: int


def price_calendar(store: ParquetStore) -> list[date_]:
    """Every day the store holds TW prices for, ascending."""
    return sorted(store.list_partition_dates(PRICE_EOD_DATASET, _MARKET))


def snapshot_dates(store: ParquetStore, dataset: str) -> list[date_]:
    return sorted(store.list_partition_dates(dataset, _MARKET))


def _snapshot_rows(
    store: ParquetStore, dataset: str, days: Sequence[date_]
) -> list[dict[str, object]]:
    return store.read_range_batched(dataset, _MARKET, days)


def load_snapshot_events(
    store: ParquetStore,
    kind: EventType,
    flt: EventDateFilter,
    calendar: Sequence[date_],
) -> tuple[ExtractionResult, EventCoverage]:
    """Events of a snapshot-fed type, read oldest snapshot first so a later snapshot's values
    replace an earlier one's for the same announcement."""
    dataset = EVENT_DATASET[kind]
    days = snapshot_dates(store, dataset)
    raw = _snapshot_rows(store, dataset, days)
    coverage = EventCoverage(
        dataset=dataset,
        first_snapshot=days[0] if days else None,
        last_snapshot=days[-1] if days else None,
        n_snapshots=len(days),
    )
    if kind in (EventType.DISPOSITION_START, EventType.DISPOSITION_END):
        disposition = rows_from_store(DispositionEventRow, raw)
        return disposition_events(disposition, kind, flt, calendar), coverage
    if kind is EventType.ATTENTION_LISTED:
        return attention_events(rows_from_store(AttentionStockRow, raw), flt), coverage
    announcements = rows_from_store(MaterialAnnouncementRow, raw)
    return buyback_events(announcements, kind, flt), coverage


def load_close_panel(store: ParquetStore, since: date_ | None) -> ClosePanel:
    """Closes and volumes of every stock and ETF for the stored days on or after `since` (all
    stored days when None). Warrants never leave the query."""
    days = [d for d in price_calendar(store) if since is None or d >= since]
    if not days:
        return ClosePanel(
            dates=(),
            symbols=(),
            close=np.empty((0, 0), dtype=np.float64),
            volume=np.empty((0, 0), dtype=np.float64),
        )
    frame = store.read_range_frame(
        PRICE_EOD_DATASET,
        _MARKET,
        days,
        columns=_PRICE_COLUMNS,
        where=scoreable_symbol_sql(Market.TW),
    )
    day_index = {d.isoformat(): i for i, d in enumerate(days)}
    mapped = cast("pd.Series", frame["trade_date"]).map(day_index)
    rows = mapped.to_numpy(dtype=np.float64)
    keep = np.isfinite(rows)
    codes, uniques = cast(
        "tuple[npt.NDArray[np.intp], pd.Index]", pd.factorize(cast("pd.Series", frame["symbol"]))
    )
    row_idx = rows[keep].astype(np.int64)
    col_idx = codes[keep]
    close = np.full((len(days), len(uniques)), np.nan, dtype=np.float64)
    volume = np.full((len(days), len(uniques)), np.nan, dtype=np.float64)
    close[row_idx, col_idx] = frame["close"].to_numpy(dtype=np.float64)[keep]
    volume[row_idx, col_idx] = frame["volume"].to_numpy(dtype=np.float64)[keep]
    return ClosePanel(
        dates=tuple(days),
        symbols=tuple(cast("list[str]", uniques.tolist())),
        close=close,
        volume=volume,
    )
