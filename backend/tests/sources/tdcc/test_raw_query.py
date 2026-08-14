"""`sources.tdcc.raw_query` tests — recorded fixture, no live network."""

from __future__ import annotations

import pytest

import sources.tdcc as tdcc_module
from schemas.enums import DataClass, TLSPolicy
from schemas.source import HealthStatus
from sources.tdcc import raw_query
from tests.sources.conftest import load_text_fixture, make_bytes_client_factory


def _patch_client(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    monkeypatch.setattr(tdcc_module, "build_async_client", make_bytes_client_factory(body))


async def test_raw_query_returns_header_and_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    text = load_text_fixture("tdcc", "1-1_securities_info.csv", encoding="utf-8-sig")
    _patch_client(monkeypatch, text.encode("utf-8-sig"))

    result = await raw_query("1-1", tls_policy=TLSPolicy.STRICT)

    assert result.source_id == "TDCC"
    assert result.data_class is DataClass.PUBLIC
    assert result.health.status is HealthStatus.OK
    header = result.payload["header"]
    assert isinstance(header, list)
    assert "證券代號" in header
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 3


async def test_raw_query_filters_by_symbol_column(monkeypatch: pytest.MonkeyPatch) -> None:
    text = load_text_fixture("tdcc", "1-1_securities_info.csv", encoding="utf-8-sig")
    _patch_client(monkeypatch, text.encode("utf-8-sig"))

    result = await raw_query(
        "1-1", tls_policy=TLSPolicy.STRICT, symbol_column="證券代號", symbol_value="2330"
    )

    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert len(rows) == 1
    assert rows[0]["證券代號"] == "2330"  # type: ignore[index]


async def test_raw_query_no_data_marker_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, b"No Data!")

    result = await raw_query("9-9", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.STALE
    assert result.payload == {}


async def test_raw_query_falls_back_to_big5_on_utf8_decode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = load_text_fixture("tdcc", "6-2_evoting_schedule_big5.csv", encoding="big5")
    _patch_client(monkeypatch, text.encode("big5"))

    result = await raw_query("6-2", tls_policy=TLSPolicy.STRICT)

    assert result.health.status is HealthStatus.OK
    rows = result.payload["rows"]
    assert isinstance(rows, list)
    assert rows[0]["證券名稱"] == "鮮活果汁-KY"  # type: ignore[index]
