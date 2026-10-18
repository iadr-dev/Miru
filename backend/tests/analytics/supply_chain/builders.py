"""Synthetic builders: invented companies and example.com sources only."""

from __future__ import annotations

from datetime import date

from analytics.supply_chain.domain.models import (
    Endpoint,
    Evidence,
    Grade,
    Kind,
    Origin,
    Relation,
    Status,
    Trust,
)

ALPHA = Endpoint(symbol="9001", market="TW", name="Alpha Parts")
BETA = Endpoint(symbol="9002", market="TW", name="Beta Devices")
GAMMA = Endpoint(symbol="ZZZA", market="US", name="Gamma Corp")


def evidence(
    trust: Trust = Trust.SECONDARY, publisher: str = "Example News", n: int = 1
) -> Evidence:
    return Evidence(
        url=f"https://{publisher.replace(' ', '-').lower()}.example/{n}",
        publisher=publisher,
        published_on=date(2025, 1, 2),
        excerpt="synthetic sentence",
        trust=trust,
    )


def relation(  # noqa: PLR0913 - a builder with many optional fields
    supplier: Endpoint = ALPHA,
    customer: Endpoint = BETA,
    *,
    kind: Kind = Kind.SUPPLIES,
    product: str | None = None,
    status: Status = Status.VERIFIED,
    grade: Grade = Grade.HIGH,
    origin: Origin = Origin.RESEARCH,
    as_of: date | None = date(2026, 1, 1),
    revenue_pct: float | None = None,
    evidence_rows: tuple[Evidence, ...] = (),
) -> Relation:
    return Relation(
        supplier=supplier,
        customer=customer,
        kind=kind,
        product=product,
        status=status,
        grade=grade,
        origin=origin,
        as_of=as_of,
        revenue_pct=revenue_pct,
        evidence=evidence_rows,
    )
