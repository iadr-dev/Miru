"""`X-Auth-Expired`: a public data route answers an expired access token anonymously (so a
logged-out visitor with a stale cookie still works) but flags the response, so the web client
refreshes and retries instead of silently showing a signed-in paid user free-tier teasers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from accounts.auth import issue_access_token
from config.settings import Settings
from schemas.enums import Tier
from tests.api.conftest import sign_in

_PUBLIC_ROUTE = "/api/v1/market/holder-accumulation"


def test_expired_token_is_served_anonymously_and_flagged(
    client: TestClient, settings: Settings
) -> None:
    _, user_id = sign_in(client, settings, tier=Tier.PRO)
    expired = issue_access_token(
        user_id=user_id,
        settings=settings.model_copy(update={"jwt_access_token_expire_minutes": -1}),
    )

    resp = client.get(_PUBLIC_ROUTE, headers={"Authorization": f"Bearer {expired}"})

    assert resp.status_code == 200
    assert resp.headers.get("X-Auth-Expired") == "1"


def test_valid_token_is_not_flagged(client: TestClient, settings: Settings) -> None:
    headers, _ = sign_in(client, settings, tier=Tier.PRO)

    resp = client.get(_PUBLIC_ROUTE, headers=headers)

    assert resp.status_code == 200
    assert "X-Auth-Expired" not in resp.headers


def test_anonymous_request_is_not_flagged(client: TestClient) -> None:
    resp = client.get(_PUBLIC_ROUTE)

    assert resp.status_code == 200
    assert "X-Auth-Expired" not in resp.headers
