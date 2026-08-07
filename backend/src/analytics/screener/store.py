"""The query-time read seam over the `screener_flags` Parquet dataset.

Given a `TagScreenerQuery`, this resolves the flag partition (the `as_of` date, or the latest
available one), reads the true `(symbol, tag)` rows through the ingest layer's read-only Parquet
reader, and computes the AND/OR match over the requested tags — returning a `TagScreenerResult`
with each hit's `matched_tags` populated (all requested tags for an AND hit; the satisfied subset
for an OR hit).

**Why this dataset is Parquet, not relational** (unlike the theme/tier/edge stores, which are
SQLAlchemy tables): the flags are genuinely date-partitioned whole-market data — one partition per
market per trading day, recomputed nightly, read back by date — which is exactly the shape the
Hive-partitioned derived store exists for, identical to how `price_eod`/`institutional_flow` are
read. Theme/tier data is slow-moving relational reference data with no date partitioning, so it
belongs in the relational store; flags do not.

**Layer placement.** This read seam lives under `analytics/` and reaches the store through the
ingest reader (same layer), not under `dataaccess/`: a `dataaccess/` module may not import
`analytics`/`ingest` under the one-way dependency rule, so it cannot host a read that composes a
derived-store reader with the tag catalog. The tool layer imports this directly, mirroring how the
theme tools read `analytics/themes/store.py`. The numeric `dataaccess/screener.py` seam sits in
`dataaccess/` only because it wraps a `sources/` adapter (a downward edge); this one does not.

**Negation semantics.** Because only true flags are stored, a negated condition ("not 外資連3買")
is evaluated against the set of symbols the day's flag partition actually contains — i.e. symbols
that carried at least one true flag. A symbol with no flag at all is absent from the partition
entirely and is not considered a negation match; this is disclosed rather than silently treated as
"the whole universe minus the tag".
"""

from __future__ import annotations

from datetime import date as date_
from pathlib import Path
from typing import Protocol

import structlog

from analytics.screener.batch import SCREENER_FLAGS_DATASET
from analytics.screener.catalog import tag_keys
from schemas.screener import (
    TagCondition,
    TagScreenerHit,
    TagScreenerQuery,
    TagScreenerResult,
)

logger = structlog.get_logger(__name__)


class FlagPartitionReader(Protocol):
    """The read-only operations this seam needs from the derived store — a structural seam so the
    match logic is unit-testable with a fake and never hard-binds the concrete Parquet reader."""

    def read_partition(
        self, dataset: str, market: str, run_date: date_
    ) -> list[dict[str, object]]: ...

    def has_partition(self, dataset: str, market: str, run_date: date_) -> bool: ...


def _condition_label(condition: TagCondition) -> str:
    """How a satisfied condition is shown in `matched_tags` — the plain key, or `not <key>` for a
    negated one, so a hit row is self-explaining without re-reading the query."""
    return f"not {condition.tag}" if condition.negate else condition.tag


def _latest_partition_date(
    reader: FlagPartitionReader, root_scan: object, market: str
) -> date_ | None:
    """Resolve the newest flag partition date for `market` by scanning the dataset directory. A
    concrete `ParquetMarketDataReader` exposes its `root`; `root_scan` is that path. Returns
    `None` when the dataset has never been written for this market."""
    if not isinstance(root_scan, (str, Path)):
        return None
    market_dir = Path(root_scan) / f"dataset={SCREENER_FLAGS_DATASET}" / f"market={market}"
    if not market_dir.exists():
        return None
    candidates: list[date_] = []
    for child in market_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("date="):
            continue
        try:
            day = date_.fromisoformat(child.name.removeprefix("date="))
        except ValueError:
            continue
        if reader.has_partition(SCREENER_FLAGS_DATASET, market, day):
            candidates.append(day)
    return max(candidates) if candidates else None


def _symbols_by_tag(rows: list[dict[str, object]]) -> tuple[dict[str, set[str]], set[str]]:
    """From the raw `(symbol, tag)` flag rows: a map of tag -> the symbols carrying it, plus the
    universe of every symbol present in the partition (used to evaluate negation)."""
    by_tag: dict[str, set[str]] = {}
    universe: set[str] = set()
    for row in rows:
        symbol = row.get("symbol")
        tag = row.get("tag")
        if not isinstance(symbol, str) or not isinstance(tag, str) or not symbol or not tag:
            continue
        by_tag.setdefault(tag, set()).add(symbol)
        universe.add(symbol)
    return by_tag, universe


def _satisfied_symbols(
    condition: TagCondition, by_tag: dict[str, set[str]], universe: set[str]
) -> set[str]:
    """The symbols satisfying one condition. A non-negated condition: the symbols flagged with the
    tag. A negated condition: the partition's symbol universe minus those flagged (see the module
    docstring for why the universe is "symbols with any flag")."""
    flagged = by_tag.get(condition.tag, set())
    return (universe - flagged) if condition.negate else set(flagged)


def evaluate_query(query: TagScreenerQuery, rows: list[dict[str, object]]) -> TagScreenerResult:
    """Pure match core over already-read flag rows — no I/O. Computes the AND/OR result and each
    hit's `matched_tags`, and reports any unknown requested tag keys honestly in `detail`."""
    known = tag_keys()
    unknown = tuple(c.tag for c in query.tags if c.tag not in known)
    by_tag, universe = _symbols_by_tag(rows)

    per_condition: list[tuple[TagCondition, set[str]]] = [
        (condition, _satisfied_symbols(condition, by_tag, universe)) for condition in query.tags
    ]
    symbol_sets: list[set[str]] = [symbols for _, symbols in per_condition]

    hits_all: list[TagScreenerHit]
    if query.match == "AND":
        matched: set[str] = set(symbol_sets[0])
        for symbols in symbol_sets[1:]:
            matched &= symbols
        # Every requested condition is satisfied for an AND hit, so its matched_tags is all of them.
        matched_labels = tuple(_condition_label(c) for c, _ in per_condition)
        hits_all = sorted(
            (TagScreenerHit(symbol=symbol, matched_tags=matched_labels) for symbol in matched),
            key=lambda hit: hit.symbol,
        )
    else:  # OR
        hits_by_symbol: dict[str, list[str]] = {}
        for condition, symbols in per_condition:
            label = _condition_label(condition)
            for symbol in symbols:
                hits_by_symbol.setdefault(symbol, []).append(label)
        hits_all = sorted(
            (
                TagScreenerHit(symbol=symbol, matched_tags=tuple(labels))
                for symbol, labels in hits_by_symbol.items()
            ),
            key=lambda hit: hit.symbol,
        )

    total = len(hits_all)
    hits = tuple(hits_all[: query.size])
    detail = _build_detail(total, query.match, unknown, len(rows))
    return TagScreenerResult(
        market=query.market,
        as_of=query.as_of,
        total_matches=total,
        hits=hits,
        detail=detail,
    )


def _build_detail(total: int, match: str, unknown: tuple[str, ...], row_count: int) -> str:
    parts: list[str] = []
    if row_count == 0:
        parts.append("no ingested flag partition for the requested date")
    parts.append(f"{total} symbol(s) matched under {match}")
    if unknown:
        parts.append(f"unknown tag key(s) ignored: {', '.join(unknown)}")
    return "; ".join(parts)


def run_tag_screener_query(
    reader: FlagPartitionReader, query: TagScreenerQuery, *, root: object = None
) -> TagScreenerResult:
    """Resolve the partition (the query's `as_of`, or the latest available when unset), read its
    flag rows, and evaluate the match. `root` is the reader's Parquet root, needed only for latest-
    partition discovery when `as_of` is unset; a concrete `ParquetMarketDataReader` exposes it as
    `.root`. Never raises on a missing partition — that degrades to an honest empty result."""
    market = query.market.value
    as_of = query.as_of
    if as_of is None:
        as_of = _latest_partition_date(reader, root, market)
    if as_of is None:
        return TagScreenerResult(
            market=query.market,
            as_of=None,
            total_matches=0,
            hits=(),
            detail=f"no {SCREENER_FLAGS_DATASET} partition has been written for market {market}",
        )
    rows = reader.read_partition(SCREENER_FLAGS_DATASET, market, as_of)
    resolved = query.model_copy(update={"as_of": as_of}) if query.as_of is None else query
    return evaluate_query(resolved, rows)


__all__ = [
    "FlagPartitionReader",
    "evaluate_query",
    "run_tag_screener_query",
]
