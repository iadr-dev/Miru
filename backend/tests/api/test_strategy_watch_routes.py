"""`/strategy-watch/subscription`: open to every signed-in tier, one per user, optimistic version
check, owned-watchlist check, per-user isolation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import sign_in

_URL = "/api/v1/strategy-watch/subscription"


def _body(**fields: object) -> dict[str, object]:
    return {"strategies": ["oversold_bounce", "volatility_squeeze"], **fields}


def test_requires_sign_in(client: TestClient) -> None:
    assert client.get(_URL).status_code == 401


def test_free_user_subscribes_and_reads_it_back(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.FREE)
    assert client.get(_URL, headers=headers).json() is None

    created = client.put(_URL, json=_body(daily_limit=5), headers=headers)

    assert created.status_code == 200
    body = created.json()
    # Stored in display order whatever order the client sent.
    assert body["strategies"] == ["volatility_squeeze", "oversold_bounce"]
    assert body["daily_limit"] == 5
    assert body["morning_list"] is True
    assert client.get(_URL, headers=headers).json()["id"] == body["id"]


def test_a_stale_version_is_a_conflict(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)
    first = client.put(_URL, json=_body(), headers=headers).json()
    client.put(_URL, json=_body(expected_version=first["version"]), headers=headers)

    stale = client.put(_URL, json=_body(expected_version=first["version"]), headers=headers)

    assert stale.status_code == 409
    assert stale.json()["code"] == "stale_version"


def test_unknown_strategy_and_foreign_watchlist_are_rejected(
    client: TestClient, settings: Settings
) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)
    assert client.put(_URL, json={"strategies": ["buy_signal"]}, headers=headers).status_code == 422
    assert client.put(_URL, json={"strategies": []}, headers=headers).status_code == 422
    missing = client.put(_URL, json=_body(watchlist_id="not-mine"), headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "watchlist_not_found"


def test_each_user_sees_only_their_own(client: TestClient, settings: Settings) -> None:
    headers_a, _ = sign_in(client, settings, tier=Tier.PRO)
    headers_b, _ = sign_in(client, settings, tier=Tier.PRO)
    client.put(_URL, json=_body(), headers=headers_a)

    assert client.get(_URL, headers=headers_b).json() is None
    assert client.delete(_URL, headers=headers_b).status_code == 404
    assert client.delete(_URL, headers=headers_a).status_code == 204
    assert client.get(_URL, headers=headers_a).json() is None
