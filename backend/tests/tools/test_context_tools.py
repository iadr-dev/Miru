from __future__ import annotations

from datetime import UTC, datetime

from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.context_tools import (
    BuildConversationPackageTool,
    GetCanonicalContextTool,
    RunBoundedObservationInput,
    RunBoundedObservationTool,
)


def _ok_result(cap: Capability) -> SourceResult:
    return SourceResult(
        source_id="STUB",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.OFFICIAL_EOD,
        fetched_at=datetime.now(UTC),
        currentness_caveat=f"caveat for {cap.value}",
        payload={"bars": [{"close": 1.0}]},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_canonical_context_delegates_and_redacts_payload() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(
        symbol,
        {
            Capability.PRICE_EOD: _ok_result(Capability.PRICE_EOD),
            Capability.FINANCIALS: _ok_result(Capability.FINANCIALS),
            Capability.DIVIDENDS: _ok_result(Capability.DIVIDENDS),
            Capability.INSTITUTIONAL_FLOW: _ok_result(Capability.INSTITUTIONAL_FLOW),
            Capability.ANALYST_CONSENSUS: _ok_result(Capability.ANALYST_CONSENSUS),
            Capability.SECTOR_CLASS: _ok_result(Capability.SECTOR_CLASS),
        },
    )
    tool = GetCanonicalContextTool(router)
    args = tool.input_model(id="2330", market=Market.TW)

    output = await tool.run(args, ToolContext())

    assert output.symbol == "2330"
    assert output.market == "TW"
    price_record = next(r for r in output.records if r.capability is Capability.PRICE_EOD)
    assert price_record.payload_keys == ("bars",)
    # never the raw payload dict/values themselves:
    assert not hasattr(output, "payload")
    for record in output.records:
        assert not hasattr(record, "payload")


async def test_build_conversation_package_has_no_raw_payload_and_carries_caveats() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, _ = make_router(symbol, {Capability.PRICE_EOD: _ok_result(Capability.PRICE_EOD)})
    tool = BuildConversationPackageTool(router)
    args = tool.input_model(id="2330", market=Market.TW)

    output = await tool.run(args, ToolContext())

    assert output.non_trading_notice
    fact = output.facts[0]
    assert fact.currentness_caveat
    assert not hasattr(fact, "payload")


async def test_run_bounded_observation_is_single_shot_and_delegates() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    router, adapter = make_router(symbol, {Capability.PRICE_EOD: _ok_result(Capability.PRICE_EOD)})
    tool = RunBoundedObservationTool(router)
    args = RunBoundedObservationInput(id="2330", market=Market.TW, capability=Capability.PRICE_EOD)

    output = await tool.run(args, ToolContext())

    assert len(adapter.calls) == 1  # exactly one fetch — no loop
    assert output.record.capability is Capability.PRICE_EOD
    assert output.bounded_note
