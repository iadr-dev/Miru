from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dataaccess.tdcc_query import TdccQueryGateway
from schemas.enums import Capability, DataClass, Market, TimingClass
from schemas.market import Symbol
from schemas.source import HealthStatus, SourceHealth, SourceResult
from tests.tools.conftest import make_router
from tools.base import ToolContext
from tools.common import MarketOnlyInput, SymbolInput
from tools.tdcc_tools import (
    GetTdccEvotingScheduleTool,
    GetTdccMonthlyCustodyAccountsTool,
    GetTdccOffshoreFundNavTool,
    GetTdccPledgedCustodySharesTool,
    GetTdccRawDatasetTool,
    GetTdccSecuritiesInfoTool,
    GetTdccShareholdingDistributionTool,
    TdccRawQueryInput,
)


def _result(payload: dict[str, object]) -> SourceResult:
    return SourceResult(
        source_id="TDCC",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload=payload,
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )


async def test_get_tdcc_securities_info_delegates_and_parses() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "name": "台積電",
        "listing_market": "上市",
        "transfer_agent": "中國信託商業銀行股代",
        "security_status": "正常",
        "scripless": "Y",
        "currency": "TWD",
        "par_value": 10.0,
        "updated_date": "2026-08-12",
    }
    router, adapter = make_router(
        symbol, {Capability.TDCC_SECURITIES_INFO: _result({"rows": [row]})}
    )
    tool = GetTdccSecuritiesInfoTool(router)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.TDCC_SECURITIES_INFO
    assert output.rows[0].name == "台積電"


async def test_get_tdcc_pledged_custody_shares_delegates_and_parses() -> None:
    symbol = Symbol(id="1623", market=Market.TW)
    row = {
        "symbol": "1623",
        "market": "TW",
        "name": "大東電",
        "as_of": "2026-08-11",
        "dispersed_custody_shares": 0,
        "specific_custody_shares": 15004757,
        "mandatory_custody_shares": 15004757,
        "issued_thousand_shares": 60000000,
    }
    router, adapter = make_router(
        symbol, {Capability.TDCC_PLEDGED_CUSTODY_SHARES: _result({"rows": [row]})}
    )
    tool = GetTdccPledgedCustodySharesTool(router)

    output = await tool.run(SymbolInput(id="1623", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.TDCC_PLEDGED_CUSTODY_SHARES
    assert output.rows[0].specific_custody_shares == 15004757


async def test_get_tdcc_shareholding_distribution_delegates_and_parses() -> None:
    symbol = Symbol(id="2330", market=Market.TW)
    row = {
        "symbol": "2330",
        "market": "TW",
        "as_of": "2026-08-07",
        "holding_level": "17",
        "holders": 3080236,
        "shares": 25932370067,
        "percent_of_custody": 100.0,
    }
    router, adapter = make_router(
        symbol, {Capability.TDCC_SHAREHOLDING_DISTRIBUTION: _result({"rows": [row]})}
    )
    tool = GetTdccShareholdingDistributionTool(router)
    assert tool.card_type == "tdcc_shareholding_distribution"

    output = await tool.run(SymbolInput(id="2330", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.TDCC_SHAREHOLDING_DISTRIBUTION
    assert output.rows[0].percent_of_custody == 100.0


async def test_get_tdcc_offshore_fund_nav_delegates_and_parses() -> None:
    symbol = Symbol(id="000AMEEBEI", market=Market.TW)
    row = {
        "symbol": "000AMEEBEI",
        "market": "TW",
        "name": "天利(盧森堡)-歐洲策略債券基金(歐元)",
        "nav_date": "2026-08-07",
        "nav": 31.6954,
        "isin_code": "LU0096353940",
        "currency": "EUR",
        "offshore_fund_institution": "天利管理(盧森堡)有限公司",
        "master_agent": "柏瑞證券投資信託股份有限公司",
    }
    router, adapter = make_router(
        symbol, {Capability.TDCC_OFFSHORE_FUND_NAV: _result({"rows": [row]})}
    )
    tool = GetTdccOffshoreFundNavTool(router)

    output = await tool.run(SymbolInput(id="000AMEEBEI", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.TDCC_OFFSHORE_FUND_NAV
    assert output.rows[0].currency == "EUR"


async def test_get_tdcc_evoting_schedule_delegates_and_parses() -> None:
    symbol = Symbol(id="1256", market=Market.TW)
    row = {
        "symbol": "1256",
        "market": "TW",
        "name": "鮮活果汁-KY",
        "fiscal_year_roc": "115",
        "meeting_date": "2026-08-11",
        "voting_start_date": "2026-07-25",
        "voting_end_date": "2026-08-08",
    }
    router, adapter = make_router(
        symbol, {Capability.TDCC_EVOTING_SCHEDULE: _result({"rows": [row]})}
    )
    tool = GetTdccEvotingScheduleTool(router)
    assert tool.card_type == "tdcc_evoting_schedule"

    output = await tool.run(SymbolInput(id="1256", market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.TDCC_EVOTING_SCHEDULE
    assert output.rows[0].meeting_date.isoformat() == "2026-08-11"


async def test_get_tdcc_monthly_custody_accounts_delegates_and_parses() -> None:
    symbol = Symbol(id="", market=Market.TW)
    row = {
        "market": "TW",
        "as_of_month": "2026-03",
        "new_accounts": 342155,
        "closed_accounts": 22287,
        "net_accounts": 319868,
        "cumulative_accounts": 25174698,
    }
    router, adapter = make_router(
        symbol, {Capability.TDCC_MONTHLY_CUSTODY_ACCOUNTS: _result({"rows": [row]})}
    )
    tool = GetTdccMonthlyCustodyAccountsTool(router)

    output = await tool.run(MarketOnlyInput(market=Market.TW), ToolContext())

    assert adapter.calls[0][0] is Capability.TDCC_MONTHLY_CUSTODY_ACCOUNTS
    assert output.rows[0].cumulative_accounts == 25174698


class _StubGateway(TdccQueryGateway):
    """Stub `TdccQueryGateway` for the raw-query catch-all tool — records the call args, never
    touches the network, proving this tool bypasses `DataAccessRouter` entirely."""

    def __init__(self, result: SourceResult) -> None:
        self._stub_result = result
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def query(
        self,
        dataset_id: str,
        *,
        tls_policy: object = None,
        symbol_column: str | None = None,
        symbol_value: str | None = None,
    ) -> SourceResult:
        self.calls.append((dataset_id, symbol_column, symbol_value))
        return self._stub_result


async def test_get_tdcc_raw_dataset_delegates_to_gateway_not_router() -> None:
    stub_result = SourceResult(
        source_id="TDCC",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="raw-query caveat",
        payload={
            "header": ["資料日期", "證券代號", "證券名稱", "登錄數額"],
            "rows": [{"資料日期": "20260811", "證券代號": "1271", "證券名稱": "晨暉生技"}],
        },
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    gateway = _StubGateway(stub_result)
    tool = GetTdccRawDatasetTool(gateway)
    assert tool.data_class is DataClass.PUBLIC

    output = await tool.run(TdccRawQueryInput(dataset_id="1-9"), ToolContext())

    assert gateway.calls[0][0] == "1-9"
    assert output.dataset_id == "1-9"
    assert output.currentness_caveat == "raw-query caveat"
    assert output.header == ("資料日期", "證券代號", "證券名稱", "登錄數額")
    assert len(output.rows) == 1
    assert not output.truncated


async def test_get_tdcc_raw_dataset_truncates_at_limit() -> None:
    rows = [{"id": str(i)} for i in range(5)]
    stub_result = SourceResult(
        source_id="TDCC",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={"header": ["id"], "rows": rows},
        health=SourceHealth(status=HealthStatus.OK, detail="ok"),
    )
    gateway = _StubGateway(stub_result)
    tool = GetTdccRawDatasetTool(gateway)

    output = await tool.run(TdccRawQueryInput(dataset_id="1-9", limit=2), ToolContext())

    assert len(output.rows) == 2
    assert output.truncated is True


@pytest.mark.parametrize(
    ("dataset_id", "symbol_column", "symbol_value"),
    [("1-9", "證券代號", "1271")],
)
async def test_get_tdcc_raw_dataset_passes_filter_through(
    dataset_id: str, symbol_column: str, symbol_value: str
) -> None:
    stub_result = SourceResult(
        source_id="TDCC",
        data_class=DataClass.PUBLIC,
        timing_class=TimingClass.REFERENCE,
        fetched_at=datetime.now(UTC),
        currentness_caveat="caveat",
        payload={"header": [], "rows": []},
        health=SourceHealth(status=HealthStatus.STALE, detail="no matching rows"),
    )
    gateway = _StubGateway(stub_result)
    tool = GetTdccRawDatasetTool(gateway)

    await tool.run(
        TdccRawQueryInput(
            dataset_id=dataset_id, symbol_column=symbol_column, symbol_value=symbol_value
        ),
        ToolContext(),
    )

    assert gateway.calls[0] == (dataset_id, symbol_column, symbol_value)
