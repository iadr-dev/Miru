"""Data-access router tests — priority + fallback, no I/O.

Uses stub adapters (never real `sources/*`) to prove the router's resolution and fallback
logic in isolation — the router itself performs no network I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime

from dataaccess.router import DataAccessRouter, load_priority_config
from schemas.enums import Capability, DataClass, Market, TimingClass, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult


class _StubAdapter:
    def __init__(
        self,
        source_id: str,
        *,
        supported: set[tuple[Capability, Market]],
        status: HealthStatus = HealthStatus.OK,
        populated: bool = True,
    ) -> None:
        self.source_id = source_id
        self.timing_class = TimingClass.OFFICIAL_EOD
        self._supported = supported
        self._status = status
        self._populated = populated
        self.calls: list[Symbol] = []

    def supports(self) -> set[tuple[Capability, Market]]:
        return self._supported

    async def fetch(
        self, cap: Capability, sym: Symbol, *, tls_policy: TLSPolicy, **params: object
    ) -> SourceResult:
        self.calls.append(sym)
        payload = {"ok": True} if self._populated else {}
        return SourceResult(
            source_id=self.source_id,
            data_class=DataClass.PUBLIC,
            timing_class=TimingClass.OFFICIAL_EOD,
            fetched_at=datetime.now(UTC),
            currentness_caveat="stub caveat",
            payload=payload,
            health=SourceHealth(status=self._status, detail="stub"),
        )

    def validate(self, raw: dict[str, object]) -> dict[str, object]:
        return raw


def _priority(table: dict[str, dict[str, list[str]]]) -> dict[str, dict[str, list[str]]]:
    return table


async def test_first_healthy_populated_wins() -> None:
    primary = _StubAdapter("primary", supported={(Capability.PRICE_EOD, Market.TW)})
    fallback = _StubAdapter("fallback", supported={(Capability.PRICE_EOD, Market.TW)})
    router = DataAccessRouter(
        {"primary": primary, "fallback": fallback},
        priority_config=_priority({"TW": {"price_eod": ["primary", "fallback"]}}),
    )

    result = await router.fetch(Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW))

    assert result.source_id == "primary"
    assert fallback.calls == []  # fallback never called — primary was healthy+populated


async def test_falls_through_on_empty_result() -> None:
    primary = _StubAdapter(
        "primary", supported={(Capability.PRICE_EOD, Market.TW)}, populated=False
    )
    fallback = _StubAdapter("fallback", supported={(Capability.PRICE_EOD, Market.TW)})
    router = DataAccessRouter(
        {"primary": primary, "fallback": fallback},
        priority_config=_priority({"TW": {"price_eod": ["primary", "fallback"]}}),
    )

    result = await router.fetch(Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW))

    assert result.source_id == "fallback"
    assert len(primary.calls) == 1


async def test_falls_through_on_unreachable() -> None:
    primary = _StubAdapter(
        "primary", supported={(Capability.PRICE_EOD, Market.TW)}, status=HealthStatus.UNREACHABLE
    )
    fallback = _StubAdapter("fallback", supported={(Capability.PRICE_EOD, Market.TW)})
    router = DataAccessRouter(
        {"primary": primary, "fallback": fallback},
        priority_config=_priority({"TW": {"price_eod": ["primary", "fallback"]}}),
    )

    result = await router.fetch(Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW))

    assert result.source_id == "fallback"


async def test_all_unhealthy_surfaces_last_result_not_a_hard_failure() -> None:
    primary = _StubAdapter(
        "primary", supported={(Capability.PRICE_EOD, Market.TW)}, status=HealthStatus.UNREACHABLE
    )
    router = DataAccessRouter(
        {"primary": primary}, priority_config=_priority({"TW": {"price_eod": ["primary"]}})
    )

    result = await router.fetch(Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW))

    assert result.source_id == "primary"
    assert result.health.status is HealthStatus.UNREACHABLE  # unavailable via health, not raise


async def test_no_configured_adapters_returns_unavailable() -> None:
    router = DataAccessRouter({}, priority_config=_priority({"TW": {}}))

    result = await router.fetch(Capability.PRICE_EOD, Symbol(id="2330", market=Market.TW))

    assert result.source_id == "none"
    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.currentness_caveat


def test_resolve_skips_adapter_not_declaring_support() -> None:
    """Config/adapter drift: listed in YAML but doesn't declare the pair — skip, don't call."""
    mismatched = _StubAdapter("mismatched", supported={(Capability.FINANCIALS, Market.TW)})
    router = DataAccessRouter(
        {"mismatched": mismatched},
        priority_config=_priority({"TW": {"price_eod": ["mismatched"]}}),
    )

    resolved = router.resolve(Market.TW, Capability.PRICE_EOD)

    assert resolved == []


def test_resolve_skips_unknown_source_id() -> None:
    router = DataAccessRouter(
        {}, priority_config=_priority({"TW": {"price_eod": ["ghost_source"]}})
    )

    resolved = router.resolve(Market.TW, Capability.PRICE_EOD)

    assert resolved == []


def test_load_priority_config_reads_real_yaml() -> None:
    config = load_priority_config()

    assert "TW" in config
    assert "finmind" in config["TW"]["price_eod"]
    assert "yfinance" in config["TW"]["analyst_consensus"]
