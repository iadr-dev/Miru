"""The priority-fallback data-access router.

Resolves `(market, capability) -> ordered adapters` from `config/source_priority.yaml` and
calls `fetch()` in priority order; the first healthy + populated result wins, else falls
through, else `unavailable` (surfaced via health, never a hard failure).

This module does **no network I/O itself** — it only calls `SourceAdapter.fetch`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

import structlog
import yaml

from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from sources.base import SourceAdapter

logger = structlog.get_logger(__name__)

_DEFAULT_CONFIG_PATH: Final[Path] = (
    Path(__file__).resolve().parents[1] / "config" / "source_priority.yaml"
)

_POPULATED_STATUSES: Final[set[HealthStatus]] = {HealthStatus.OK}


def load_priority_config(path: Path | None = None) -> dict[str, dict[str, list[str]]]:
    """Load `(market -> capability -> [source_id, ...])` from YAML. Data, not code."""
    config_path = path or _DEFAULT_CONFIG_PATH
    with config_path.open(encoding="utf-8") as fh:
        raw: object = yaml.safe_load(fh) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"source_priority.yaml at {config_path} did not parse to a mapping")
    return cast("dict[str, dict[str, list[str]]]", raw)


def _unavailable_result(cap: Capability, sym: Symbol) -> SourceResult:
    return SourceResult(
        source_id="none",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat=(
            f"No healthy source available for capability {cap.value} on "
            f"{sym.market.value}:{sym.id} — surfaced via source health, not a hard failure."
        ),
        payload={},
        health=SourceHealth(
            status=HealthStatus.UNREACHABLE,
            detail="all configured adapters unavailable, unhealthy, or empty",
        ),
    )


class DataAccessRouter:
    """Resolves a capability request to an ordered adapter list and tries them in order."""

    def __init__(
        self,
        adapters: dict[str, SourceAdapter],
        priority_config: dict[str, dict[str, list[str]]] | None = None,
    ) -> None:
        self._adapters = adapters
        self._priority = priority_config if priority_config is not None else load_priority_config()

    def source_matrix(self) -> dict[str, frozenset[tuple[Capability, Market]]]:
        """`{source_id -> {(capability, market), ...}}` for every configured adapter.

        Exists so `tools/health_tools.py`'s `get_source_matrix` tool can render the adapter
        matrix without importing `sources.base.SourceAdapter` itself (`tools/` may not import
        `sources/` directly — enforced by the architecture layering test).
        """
        return {
            source_id: frozenset(adapter.supports())
            for source_id, adapter in self._adapters.items()
        }

    def resolve(self, market: Market, cap: Capability) -> list[SourceAdapter]:
        """`(market, capability) -> ordered [adapters]` — config-driven, never caller-driven."""
        market_table = self._priority.get(market.value, {})
        source_ids = market_table.get(cap.value, [])
        resolved: list[SourceAdapter] = []
        for source_id in source_ids:
            adapter = self._adapters.get(source_id)
            if adapter is None:
                logger.warning(
                    "dataaccess_unknown_source_id",
                    source_id=source_id,
                    market=market.value,
                    cap=cap.value,
                )
                continue
            if (cap, market) not in adapter.supports():
                # Adapter is listed in priority config but doesn't declare this pair —
                # config/adapter drift; skip rather than call something unsupported.
                continue
            resolved.append(adapter)
        return resolved

    async def fetch(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        **params: object,
    ) -> SourceResult:
        """Try adapters in priority order; first healthy+populated result wins."""
        adapters = self.resolve(sym.market, cap)
        last_result: SourceResult | None = None
        for adapter in adapters:
            result = await adapter.fetch(cap, sym, tls_policy=tls_policy, **params)
            last_result = result
            if result.health.status in _POPULATED_STATUSES and result.payload:
                return result
            logger.info(
                "dataaccess_fallthrough",
                source_id=adapter.source_id,
                cap=cap.value,
                symbol=sym.id,
                market=sym.market.value,
                health=result.health.status.value,
            )
        if last_result is not None:
            # Every adapter tried but none was healthy+populated — surface the last
            # attempt's health rather than fabricate one, but tag as router-level unavailable
            # if literally nothing was configured at all.
            return last_result
        return _unavailable_result(cap, sym)

    async def fetch_all(
        self,
        cap: Capability,
        sym: Symbol,
        *,
        tls_policy: TLSPolicy = TLSPolicy.STRICT,
        **params: object,
    ) -> list[SourceResult]:
        """Call every configured adapter for `(market, capability)` and return each one's
        result, never stopping at the first healthy one.

        Distinct from `fetch()`'s priority-fallback semantics on purpose: some capabilities
        (`calendar_events`) legitimately have multiple adapters that each contribute a disjoint
        slice of the same logical feed (NCDR closures + TWSE AGM/ex-rights schedules both apply
        to a stock's calendar at once) rather than one adapter superseding another. A caller
        that wants first-healthy-wins semantics still uses `fetch()`; a caller that wants to
        merge every adapter's contribution uses this instead."""
        adapters = self.resolve(sym.market, cap)
        results: list[SourceResult] = []
        for adapter in adapters:
            results.append(await adapter.fetch(cap, sym, tls_policy=tls_policy, **params))
        return results


__all__ = ["DataAccessRouter", "load_priority_config"]
