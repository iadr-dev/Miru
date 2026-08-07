"""Canonical (M5F, Mode A / Level 1) — `context/canonical.py`.

Builds and validates the **immutable** canonical market-context package from the data-access
router's results. An existing canonical package is never mutated;
any "update" is a **new version** of a **new** frozen object — never a setter, never an
in-place field assignment. `CanonicalPackage` is a Pydantic v2 `frozen` model, so an attempted
mutation raises `ValidationError` at the framework level (enforced in code, not just by
convention — see `tests/context/test_canonical.py`).

This module performs **no I/O** — it only assembles `SourceResult`s already fetched by the
caller (`context/builder.py`) into the versioned, immutable package.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import Capability
from schemas.market import Symbol
from schemas.source import SourceResult


class CanonicalRecord(BaseModel):
    """One capability's `SourceResult`, preserved verbatim under the canonical package."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    capability: Capability
    result: SourceResult


class CanonicalPackage(BaseModel):
    """M5F — the immutable, preserved market-context package for one `(symbol, market)`.

    `version` is append-only: `new_version()` never mutates `self`, it returns a **new**
    `CanonicalPackage` with `version + 1` and the given records. The frozen `model_config`
    means any attempted attribute assignment (`pkg.version = 2`) raises a pydantic
    `ValidationError` — this is the enforcement, not merely an unwritten convention.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: Symbol
    version: int = Field(default=1, ge=1)
    built_at: datetime
    records: tuple[CanonicalRecord, ...] = Field(default_factory=tuple)

    def get(self, capability: Capability) -> CanonicalRecord | None:
        """Look up the record for one capability, or `None` if it wasn't part of this build."""
        for record in self.records:
            if record.capability is capability:
                return record
        return None

    def new_version(self, records: tuple[CanonicalRecord, ...]) -> CanonicalPackage:
        """Return a brand-new `CanonicalPackage` at `version + 1`. Never mutates `self`.

        This is the *only* sanctioned "update" path: a fresh, independently
        constructed, still-frozen object — `self` is left completely untouched.
        """
        return CanonicalPackage(
            symbol=self.symbol,
            version=self.version + 1,
            built_at=datetime.now(UTC),
            records=records,
        )


def build_canonical_package(
    symbol: Symbol,
    results: dict[Capability, SourceResult],
    *,
    version: int = 1,
) -> CanonicalPackage:
    """Construct a fresh (`version`) canonical package from already-fetched router results.

    Called by `context/builder.py` (M8A/B/C) after it has resolved every requested capability
    via the data-access router — this function does no fetching itself.
    """
    records = tuple(
        CanonicalRecord(capability=cap, result=result) for cap, result in results.items()
    )
    return CanonicalPackage(
        symbol=symbol,
        version=version,
        built_at=datetime.now(UTC),
        records=records,
    )


__all__ = ["CanonicalPackage", "CanonicalRecord", "build_canonical_package"]
