"""The `/macro/us/*` REST routes, driven through the real FastAPI app over a store filled by the
real ingest job (fake service, no network). The data is public: anonymous and free callers get the
same real answer a paying one does."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Market, Tier
from schemas.market import Symbol
from tests.api.conftest import build_test_client, sign_in
from tests.fred_support import FakeFredServer, fill_store
from tests.tools.conftest import make_router
from tools import build_default_tool_registry


@pytest.fixture(scope="module")
def filled_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("us_macro_routes")
    server = FakeFredServer()
    fill_store(root, server)
    return root


def _client(
    settings: Settings, agent_answer: str, root: Path, monkeypatch: pytest.MonkeyPatch
) -> TestClient:
    monkeypatch.setenv("PARQUET_ROOT", str(root))
    client = build_test_client(settings, agent_answer=agent_answer)
    router, _ = make_router(Symbol(id="2330", market=Market.TW), {})
    object.__setattr__(
        client.app.state.app_state, "tool_registry", build_default_tool_registry(router)
    )
    return client


@pytest.fixture
def client(
    settings: Settings, agent_answer: str, filled_root: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    with _client(settings, agent_answer, filled_root, monkeypatch) as test_client:
        yield test_client


def test_dashboard_is_open_to_anonymous_callers(client: TestClient) -> None:
    resp = client.get("/api/v1/macro/us/dashboard")

    assert resp.status_code == 200
    body = resp.json()
    assert "locked" not in body
    assert body["data_available"] is True
    assert [p["group"] for p in body["panels"]][:2] == ["yield_curve_recession", "inflation"]
    assert body["currentness_caveat"]
    assert {s["series_id"] for s in body["yield_curve"]} == {"T10Y2Y", "T10Y3M"}
    assert body["recession_bands"]
    first_series = body["panels"][0]["series"][0]
    assert {"latest", "prior", "change", "sparkline", "next_release_date"} <= set(first_series)


def test_series_route_returns_points_and_metadata(client: TestClient) -> None:
    resp = client.get("/api/v1/macro/us/series", params={"series_id": "UNRATE", "limit": 12})

    assert resp.status_code == 200
    body = resp.json()
    assert body["series_id"] == "UNRATE"
    assert len(body["points"]) == 12
    assert body["total_observations"] == 40
    assert body["metadata"]["title"] == "Title of UNRATE"
    assert body["metadata"]["higher_means"] == "higher_unemployment_share"
    assert body["observation_range"]["end"] == "2026-10-01"


def test_series_route_passes_units_frequency_and_dates(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/macro/us/series",
        params={
            "series_id": "CPIAUCSL",
            "units": "lin",
            "frequency": "q",
            "start_date": "2025-01-01",
            "end_date": "2026-06-30",
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["units"] == "lin"
    assert body["frequency"] == "q"
    assert all("2025-01-01" <= p["date"] <= "2026-06-30" for p in body["points"])


def test_series_route_rejects_an_unknown_series_and_a_bad_units_combination(
    client: TestClient,
) -> None:
    assert client.get("/api/v1/macro/us/series", params={"series_id": "NOPE"}).status_code == 422
    assert client.get("/api/v1/macro/us/series").status_code == 422
    bad_units = client.get("/api/v1/macro/us/series", params={"series_id": "DGS10", "units": "pc1"})
    assert bad_units.status_code == 422


def test_releases_route_returns_the_release_feed(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/macro/us/releases", params={"from_date": "2026-09-01", "to_date": "2026-12-31"}
    )

    assert resp.status_code == 200
    body = resp.json()
    names = {r["release_name"] for r in body["releases"]}
    assert "Employment Situation" in names
    assert all(r["status"] in {"upcoming", "released"} for r in body["releases"])
    assert "consensus" not in resp.text


def test_releases_route_rejects_an_inverted_range(client: TestClient) -> None:
    resp = client.get(
        "/api/v1/macro/us/releases", params={"from_date": "2026-12-31", "to_date": "2026-09-01"}
    )

    assert resp.status_code == 422


def test_revisions_route_returns_first_release_and_latest(client: TestClient) -> None:
    resp = client.get("/api/v1/macro/us/revisions", params={"series_id": "PAYEMS"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["series_id"] == "PAYEMS"
    row = body["rows"][0]
    assert {"first_release_value", "latest_value", "revision", "vintages"} <= set(row)


def test_revisions_route_rejects_an_untracked_series(client: TestClient) -> None:
    assert (
        client.get("/api/v1/macro/us/revisions", params={"series_id": "DGS10"}).status_code == 422
    )


@pytest.mark.parametrize("tier", [Tier.FREE, Tier.PREMIUM, Tier.PRO])
def test_every_signed_in_tier_gets_the_same_unmasked_data(
    client: TestClient, settings: Settings, tier: Tier
) -> None:
    headers, _ = sign_in(client, settings, tier=tier)

    resp = client.get("/api/v1/macro/us/dashboard", headers=headers)

    assert resp.status_code == 200
    assert "locked" not in resp.json()
    assert resp.json()["data_available"] is True


def test_an_unloaded_store_answers_calmly_not_with_an_error(
    settings: Settings, agent_answer: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings, agent_answer, tmp_path, monkeypatch)

    dashboard = client.get("/api/v1/macro/us/dashboard")
    series = client.get("/api/v1/macro/us/series", params={"series_id": "DGS10"})
    releases = client.get("/api/v1/macro/us/releases")
    revisions = client.get("/api/v1/macro/us/revisions", params={"series_id": "GDPC1"})

    for resp in (dashboard, series, releases, revisions):
        assert resp.status_code == 200
        body = resp.json()
        assert body["data_available"] is False
        assert body["unavailable_reason"]
        assert body["currentness_caveat"]
        assert "FRED" not in resp.text
