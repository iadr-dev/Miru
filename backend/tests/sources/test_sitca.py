"""SITCA adapter tests — recorded fixtures, no live network (default profile)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import sources.sitca as sitca_module
from schemas.enums import Capability, DataClass, Market, TLSPolicy
from schemas.market import Symbol
from schemas.source import HealthStatus
from sources.sitca import SitcaAdapter, fetch_whole_market_etf_fees

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "sitca"

# SITCA's own report carries no ticker, only 基金統編 (tax ID) — every test stubs
# `fetch_ticker_tax_id_map` (the TWSE-sourced resolver `sitca.py` imports from `etf_pcf.py`) with
# this fixed map instead of hitting TWSE, mirroring the real tax IDs in `fee_result.html`.
_TAX_ID_MAP = {"00981A": "38619904", "0050": "98764389"}


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


async def _fake_tax_id_map(**_kwargs: object) -> dict[str, str]:
    return dict(_TAX_ID_MAP)


def _client_factory(form_html: str, result_html: str) -> object:
    """A MockTransport that serves the form on GET and the fee table on POST, and asserts the
    postback replayed the hidden fields + selected the fee-column mode + latest month."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = request.content.decode("utf-8")
            assert "__VIEWSTATE=VS_TOKEN_ABC" in body
            assert "__EVENTVALIDATION=EV_TOKEN_XYZ" in body
            assert "ddlQ_Column=2" in body
            assert "ddlQ_YYYYMM=202607" in body  # latest month, not 202606
            return httpx.Response(200, text=result_html)
        return httpx.Response(200, text=form_html)

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def test_supports_declares_tw_etf_expense_ratio() -> None:
    assert SitcaAdapter().supports() == {(Capability.ETF_EXPENSE_RATIO, Market.TW)}


def test_validate_rejects_non_object_info() -> None:
    adapter = SitcaAdapter()
    with pytest.raises(ValueError, match="not an object"):
        adapter.validate({"info": ["not", "an", "object"]})


async def test_fetch_parses_fee_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sitca_module,
        "build_async_client",
        _client_factory(_load("fee_form.html"), _load("fee_result.html")),
    )
    monkeypatch.setattr(sitca_module, "fetch_ticker_tax_id_map", _fake_tax_id_map)
    adapter = SitcaAdapter()
    sym = Symbol(id="00981A", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_EXPENSE_RATIO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "SITCA"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    assert result.currentness_caveat
    info = result.payload["info"]
    assert isinstance(info, dict)
    assert info["symbol"] == "00981A"
    assert info["management_fee_pct"] == 1.20
    assert info["custody_fee_pct"] == 0.12
    # TER approximated as management + custody (a floor, per the schema caveat).
    assert info["total_expense_ratio_pct_approx"] == pytest.approx(1.32)
    assert info["custodian_bank"] == "國泰世華商業銀行"


async def test_fetch_unknown_fund_is_stale_not_fabricated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sitca_module,
        "build_async_client",
        _client_factory(_load("fee_form.html"), _load("fee_result.html")),
    )
    monkeypatch.setattr(sitca_module, "fetch_ticker_tax_id_map", _fake_tax_id_map)
    adapter = SitcaAdapter()
    sym = Symbol(id="9999X", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_EXPENSE_RATIO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_fetch_transport_error_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(sitca_module, "build_async_client", factory)
    adapter = SitcaAdapter()
    sym = Symbol(id="00981A", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_EXPENSE_RATIO, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload == {}


async def test_fetch_wrong_capability_is_unavailable_tier() -> None:
    adapter = SitcaAdapter()
    sym = Symbol(id="00981A", market=Market.TW)

    result = await adapter.fetch(Capability.ETF_HOLDINGS, sym, tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNAVAILABLE_TIER


async def test_fetch_whole_market_returns_every_fund_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ingest-layer whole-market fetch must return every fund's fee row in one call — not
    just the one `SitcaAdapter.fetch` would filter down to per-symbol."""
    monkeypatch.setattr(
        sitca_module,
        "build_async_client",
        _client_factory(_load("fee_form.html"), _load("fee_result.html")),
    )
    monkeypatch.setattr(sitca_module, "fetch_ticker_tax_id_map", _fake_tax_id_map)

    result = await fetch_whole_market_etf_fees(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    symbols = {row["symbol"] for row in rows}
    assert "00981A" in symbols
    assert "0050" in symbols
    row_00981a = next(r for r in rows if r["symbol"] == "00981A")
    assert row_00981a["management_fee_pct"] == 1.20


async def test_fetch_whole_market_transport_error_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def factory(**_kwargs: object) -> httpx.AsyncClient:
        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(sitca_module, "build_async_client", factory)

    result = await fetch_whole_market_etf_fees(tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.UNREACHABLE
    assert result.payload["rows"] == []


async def test_fetch_whole_market_unpublished_month_is_flagged_not_layout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A month past the dropdown's newest option (SITCA publishes with a lag) must be reported as
    'not yet published', never as the layout-change failure — and must not POST at all."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, text=_load("fee_form.html"))

    def factory(**_kwargs: object) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(sitca_module, "build_async_client", factory)

    result = await fetch_whole_market_etf_fees(tls_policy=TLSPolicy.STRICT, year_month="202612")

    assert result.health.status is HealthStatus.STALE
    assert result.health.detail == sitca_module.MONTH_NOT_PUBLISHED_DETAIL
    assert result.payload == {"rows": [], sitca_module.MONTH_NOT_PUBLISHED_KEY: True}
