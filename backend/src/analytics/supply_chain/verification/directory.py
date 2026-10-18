"""Which companies exist, what they are called and how big they are.

The directory is the universe the pipeline may name: a company the model proposes that is not in it
is dropped (the same fabrication safeguard as the theme research), and the names and aliases in it
are what the guards look for inside an excerpt.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

import structlog

from analytics.fundamentals import MARKET_VALUE_DATASET
from analytics.scoring_batch.datasets import STOCK_INFO_DATASET
from analytics.scoring_batch.inputs import latest_market_value_by_symbol
from analytics.supply_chain.domain.concepts import Entity
from analytics.supply_chain.domain.models import MarketCode, Relation
from analytics.supply_chain.entities_io.loader import CATALOG_FILE, EntityFileError, parse_catalog
from analytics.supply_chain.verification.guards import squash
from analytics.supply_chain.verification.models import Company
from ingest.store import ParquetStore

logger = structlog.get_logger(__name__)

_MARKET_VALUE_DAYS = 5
_TW = "TW"


class CompanyDirectory:
    def __init__(self, companies: Iterable[Company]) -> None:
        self._by_key: dict[tuple[MarketCode, str], Company] = {c.key: c for c in companies}
        index: dict[str, set[tuple[MarketCode, str]]] = defaultdict(set)
        for company in self._by_key.values():
            for name in company.names:
                if folded := squash(name).casefold():
                    index[folded].add(company.key)
        # A name shared by two companies is ambiguous and resolves to neither.
        self._by_name = {n: next(iter(keys)) for n, keys in index.items() if len(keys) == 1}

    def get(self, market: MarketCode, symbol: str) -> Company | None:
        return self._by_key.get((market, symbol))

    def resolve_name(self, name: str) -> Company | None:
        key = self._by_name.get(squash(name).casefold())
        return self._by_key[key] if key else None

    def companies(self, market: MarketCode | None = None) -> list[Company]:
        return [c for c in self._by_key.values() if market is None or c.market == market]

    def __len__(self) -> int:
        return len(self._by_key)


def build_directory(
    *,
    roster: Mapping[str, str],
    market_values: Mapping[str, float],
    entities: Iterable[Entity] = (),
    relation_endpoints: Iterable[tuple[MarketCode, str, str]] = (),
) -> CompanyDirectory:
    """Roster names first (they are the display names), then catalog aliases, then names already
    used in the relation files. US companies exist only through the catalog or the files."""
    names: dict[tuple[MarketCode, str], list[str]] = defaultdict(list)

    def add(market: MarketCode, symbol: str, name: str) -> None:
        if name and name not in names[(market, symbol)]:
            names[(market, symbol)].append(name)

    for symbol, name in roster.items():
        add("TW", symbol, name.strip())
    for entity in entities:
        if entity.symbol and entity.market:
            add(entity.market, entity.symbol, entity.id)
            for alias in entity.aliases:
                add(entity.market, entity.symbol, alias)
    for market, symbol, name in relation_endpoints:
        add(market, symbol, name.strip())
    return CompanyDirectory(
        Company(
            symbol=symbol,
            market=market,
            names=tuple(found),
            market_value=float(market_values.get(symbol, 0.0)) if market == _TW else 0.0,
        )
        for (market, symbol), found in names.items()
    )


def endpoints_of(relations: Iterable[Relation]) -> list[tuple[MarketCode, str, str]]:
    out: list[tuple[MarketCode, str, str]] = []
    for r in relations:
        out.append((r.supplier.market, r.supplier.symbol, r.supplier.name))
        out.append((r.customer.market, r.customer.symbol, r.customer.name))
    return out


def _roster(store: ParquetStore) -> dict[str, str]:
    dates = store.list_partition_dates(STOCK_INFO_DATASET, _TW)
    if not dates:
        return {}
    rows = store.read_partition(STOCK_INFO_DATASET, _TW, max(dates))
    return {
        str(r["symbol"]): str(r.get("name", ""))
        for r in rows
        if isinstance(r.get("symbol"), str) and r.get("symbol")
    }


def _market_values(store: ParquetStore) -> dict[str, float]:
    dates = store.list_partition_dates(MARKET_VALUE_DATASET, _TW)
    rows: list[dict[str, object]] = []
    for day in sorted(dates)[-_MARKET_VALUE_DAYS:]:
        rows.extend(store.read_partition(MARKET_VALUE_DATASET, _TW, day))
    return latest_market_value_by_symbol(rows) if rows else {}


def _catalog(root: Path) -> list[Entity]:
    path = root / "entities" / CATALOG_FILE
    if not path.is_file():
        return []
    try:
        return parse_catalog(path)
    except EntityFileError:
        logger.warning("sc_research_catalog_unreadable")
        return []


def load_directory(
    store: ParquetStore, root: Path, relations: Iterable[Relation] = ()
) -> CompanyDirectory:
    """Read-only: the stored roster and market values, the entity catalog, the relation names."""
    roster = _roster(store)
    if not roster:
        logger.warning("sc_research_roster_missing")
    return build_directory(
        roster=roster,
        market_values=_market_values(store),
        entities=_catalog(root),
        relation_endpoints=endpoints_of(relations),
    )


__all__ = [
    "CompanyDirectory",
    "build_directory",
    "endpoints_of",
    "load_directory",
]
