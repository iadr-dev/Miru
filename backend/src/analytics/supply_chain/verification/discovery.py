"""Which claims to check next. Pure: callers hand in the files' contents, nothing is read here
except the candidates file reader.

Three sources, in priority order: (a) the private candidates file, where both ends must resolve to
a real company; (b) coverage gaps, the biggest companies with no published relation; (c) stale
re-checks of published research rows. Bigger companies first. A claim rejected within the cooldown
is skipped, and so is a claim already published.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import structlog
from pydantic import BaseModel, ConfigDict, ValidationError

from analytics.supply_chain.domain.concepts import market_of_symbol
from analytics.supply_chain.domain.models import Kind, MarketCode, Origin, Relation, Status
from analytics.supply_chain.verification.directory import CompanyDirectory
from analytics.supply_chain.verification.models import (
    Company,
    Target,
    TargetSource,
    claim_id_for,
    gap_id_for,
)

logger = structlog.get_logger(__name__)

CANDIDATES_FILE = ("supply_chain", "candidates.jsonl")
_WEIGHTS: dict[TargetSource, float] = {
    TargetSource.CANDIDATES: 0.6,
    TargetSource.GAPS: 0.25,
    TargetSource.STALE: 0.15,
}
_ORDER = (TargetSource.CANDIDATES, TargetSource.GAPS, TargetSource.STALE)
_GAP_SYMBOL_LEN = 4


class CandidateRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    supplier: str
    customer: str
    counterparty_name: str = ""
    counterparty_resolved: bool = False
    kind: Kind = Kind.SUPPLIES


@dataclass(frozen=True)
class DiscoveryConfig:
    limit: int = 25
    sources: frozenset[TargetSource] = frozenset(TargetSource)
    reverify_days: int = 365
    cooldown_days: int = 90
    gap_top_n: int = 50


def read_candidates(path: Path) -> list[CandidateRow]:
    """Tolerant: a blank, torn or foreign line is skipped. A missing file is no candidates."""
    if not path.is_file():
        return []
    rows: list[CandidateRow] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(CandidateRow.model_validate(json.loads(line)))
        except (ValueError, ValidationError):
            continue
    return rows


def _end(code: str, row: CandidateRow, directory: CompanyDirectory) -> Company | None:
    """A real company for one end of a candidate row, or None when it does not resolve."""
    symbol = code.strip()
    market = market_of_symbol(symbol)
    if market is None or not row.counterparty_resolved:
        return None
    known = directory.get(market, symbol)
    if market == "TW":
        return known
    if known is not None:
        return known
    name = row.counterparty_name.strip()
    return Company(symbol=symbol, market="US", names=(name,) if name else ())


def _rank(*companies: Company) -> float:
    return max(c.market_value for c in companies)


def candidate_targets(rows: Iterable[CandidateRow], directory: CompanyDirectory) -> list[Target]:
    seen: set[str] = set()
    targets: list[Target] = []
    for row in rows:
        supplier, customer = _end(row.supplier, row, directory), _end(row.customer, row, directory)
        if supplier is None or customer is None or supplier.key == customer.key:
            continue
        claim_id = claim_id_for(supplier, customer, row.kind)
        if claim_id in seen:
            continue
        seen.add(claim_id)
        targets.append(
            Target(
                claim_id=claim_id,
                source=TargetSource.CANDIDATES,
                supplier=supplier,
                customer=customer,
                kind=row.kind,
                priority=_rank(supplier, customer),
            )
        )
    return _by_priority(targets)


def _by_priority(targets: Iterable[Target]) -> list[Target]:
    return sorted(targets, key=lambda t: (-t.priority, t.claim_id))


def _pair(relation: Relation) -> tuple[MarketCode, str, MarketCode, str, Kind]:
    return (
        relation.supplier.market,
        relation.supplier.symbol,
        relation.customer.market,
        relation.customer.symbol,
        relation.kind,
    )


def gap_targets(
    directory: CompanyDirectory, published: Sequence[Relation], top_n: int
) -> list[Target]:
    """The `top_n` biggest Taiwan companies that no published relation names."""
    covered = {(r.supplier.market, r.supplier.symbol) for r in published} | {
        (r.customer.market, r.customer.symbol) for r in published
    }
    ranked = sorted(
        (
            c
            for c in directory.companies("TW")
            if c.market_value > 0 and len(c.symbol) == _GAP_SYMBOL_LEN and c.symbol.isdigit()
        ),
        key=lambda c: (-c.market_value, c.symbol),
    )[:top_n]
    return [
        Target(
            claim_id=gap_id_for(c),
            source=TargetSource.GAPS,
            supplier=c,
            priority=c.market_value,
        )
        for c in ranked
        if c.key not in covered
    ]


def stale_targets(
    directory: CompanyDirectory,
    published: Sequence[Relation],
    *,
    today: date,
    reverify_days: int,
) -> list[Target]:
    """Published research rows last verified before the cutoff (never verified counts as old)."""
    cutoff = today - timedelta(days=reverify_days)
    targets: list[Target] = []
    for relation in published:
        if relation.origin is not Origin.RESEARCH or relation.status not in (
            Status.VERIFIED,
            Status.STALE,
        ):
            continue
        checked = relation.last_verified_at.date() if relation.last_verified_at else relation.as_of
        if checked is not None and checked >= cutoff:
            continue
        supplier = directory.get(relation.supplier.market, relation.supplier.symbol) or Company(
            symbol=relation.supplier.symbol,
            market=relation.supplier.market,
            names=(relation.supplier.name,) if relation.supplier.name else (),
        )
        customer = directory.get(relation.customer.market, relation.customer.symbol) or Company(
            symbol=relation.customer.symbol,
            market=relation.customer.market,
            names=(relation.customer.name,) if relation.customer.name else (),
        )
        targets.append(
            Target(
                claim_id=claim_id_for(supplier, customer, relation.kind),
                source=TargetSource.STALE,
                supplier=supplier,
                customer=customer,
                kind=relation.kind,
                product=relation.product,
                priority=_rank(supplier, customer),
            )
        )
    return _by_priority({t.claim_id: t for t in targets}.values())


def _allocate(pools: dict[TargetSource, list[Target]], limit: int) -> list[Target]:
    """Split `limit` between the sources by weight, then hand unused room to the next in order."""
    active = [s for s in _ORDER if s in pools]
    total = sum(_WEIGHTS[s] for s in active) or 1.0
    taken: dict[TargetSource, list[Target]] = {}
    for source in active:
        quota = int(limit * _WEIGHTS[source] / total)
        taken[source] = pools[source][:quota]
    room = limit - sum(len(v) for v in taken.values())
    for source in active:
        if room <= 0:
            break
        extra = pools[source][len(taken[source]) : len(taken[source]) + room]
        taken[source] = taken[source] + extra
        room -= len(extra)
    return [t for s in active for t in taken[s]]


def discover_targets(  # noqa: PLR0913 - keyword-only inputs
    *,
    directory: CompanyDirectory,
    candidates: Sequence[CandidateRow],
    published: Sequence[Relation],
    rejected: set[str],
    today: date | None = None,
    config: DiscoveryConfig | None = None,
) -> list[Target]:
    cfg = config or DiscoveryConfig()
    day = today or datetime.now(UTC).date()
    known = {_pair(r) for r in published}

    def fresh(target: Target) -> bool:
        if target.claim_id in rejected:
            return False
        if target.customer is None:
            return True
        return (
            target.supplier.market,
            target.supplier.symbol,
            target.customer.market,
            target.customer.symbol,
            target.kind,
        ) not in known

    pools: dict[TargetSource, list[Target]] = {}
    if TargetSource.CANDIDATES in cfg.sources:
        pools[TargetSource.CANDIDATES] = [
            t for t in candidate_targets(candidates, directory) if fresh(t)
        ]
    if TargetSource.GAPS in cfg.sources:
        pools[TargetSource.GAPS] = [
            t for t in gap_targets(directory, published, cfg.gap_top_n) if fresh(t)
        ]
    if TargetSource.STALE in cfg.sources:
        # A published row is by definition "known", so the freshness test does not apply; only
        # the rejection cooldown does.
        pools[TargetSource.STALE] = [
            t
            for t in stale_targets(directory, published, today=day, reverify_days=cfg.reverify_days)
            if t.claim_id not in rejected
        ]
    return _allocate(pools, cfg.limit)


def configured_sources(name: str) -> frozenset[TargetSource]:
    if name == "all":
        return frozenset(TargetSource)
    return frozenset({TargetSource(name)})


__all__ = [
    "CANDIDATES_FILE",
    "CandidateRow",
    "DiscoveryConfig",
    "candidate_targets",
    "configured_sources",
    "discover_targets",
    "gap_targets",
    "read_candidates",
    "stale_targets",
]
