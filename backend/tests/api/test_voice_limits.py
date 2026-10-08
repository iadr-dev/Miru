"""Voice is Free but never unmetered: a per-minute rate and a daily cap, refunded on failure."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

import api.routers.voice as voice_router
from accounts import entitlements
from schemas.enums import Tier
from tests.api.conftest import sign_in

pytestmark = pytest.mark.governance

AUDIO = base64.b64encode(b"audio-bytes").decode("ascii")


def _speak(client: TestClient, headers: dict[str, str]):
    return client.post("/api/v1/voice/speak", json={"text": "你好"}, headers=headers)


def _transcribe(client: TestClient, headers: dict[str, str]):
    return client.post("/api/v1/voice/transcribe", json={"audio_base64": AUDIO}, headers=headers)


def test_the_daily_cap_applies_to_each_kind_separately(
    client: TestClient, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_router, "VOICE_DAILY_LIMIT", 2)
    monkeypatch.setattr(voice_router, "VOICE_REQUESTS_PER_MINUTE", 1000)
    headers, _ = sign_in(client, settings, tier=Tier.FREE)

    assert [_speak(client, headers).status_code for _ in range(2)] == [200, 200]
    blocked = _speak(client, headers)
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "voice_limit_reached"
    # read-aloud being used up does not take away speech-to-text
    assert _transcribe(client, headers).status_code == 200


def test_the_per_minute_rate_is_enforced(
    client: TestClient, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_router, "VOICE_REQUESTS_PER_MINUTE", 2)
    monkeypatch.setattr(voice_router, "VOICE_DAILY_LIMIT", 1000)
    headers, _ = sign_in(client, settings, tier=Tier.FREE)

    assert [_speak(client, headers).status_code for _ in range(2)] == [200, 200]
    limited = _speak(client, headers)
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"


def test_limits_are_per_user(client: TestClient, settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_router, "VOICE_DAILY_LIMIT", 1)
    monkeypatch.setattr(voice_router, "VOICE_REQUESTS_PER_MINUTE", 1000)
    first, _ = sign_in(client, settings, tier=Tier.FREE)
    second, _ = sign_in(client, settings, tier=Tier.FREE)
    assert _speak(client, first).status_code == 200
    assert _speak(client, first).status_code == 429
    assert _speak(client, second).status_code == 200


def test_a_provider_failure_gives_the_claim_back(
    client: TestClient, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(voice_router, "VOICE_DAILY_LIMIT", 1)
    monkeypatch.setattr(voice_router, "VOICE_REQUESTS_PER_MINUTE", 1000)
    headers, _ = sign_in(client, settings, tier=Tier.FREE)

    async def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("provider down")

    real = voice_router.speak_service
    monkeypatch.setattr(voice_router, "speak_service", boom)
    with pytest.raises(RuntimeError):
        _speak(client, headers)
    monkeypatch.setattr(voice_router, "speak_service", real)
    # the failed attempt did not use up the single allowed request
    assert _speak(client, headers).status_code == 200


def test_the_published_bounds_are_sane() -> None:
    assert entitlements.VOICE_DAILY_LIMIT >= 1
    assert entitlements.VOICE_REQUESTS_PER_MINUTE >= 1


def test_tick_and_minute_k_row_matches_what_the_tools_enforce() -> None:
    # Those tools are sponsor-class data, unlocked at Premium - the row must say so, not Pro.
    assert entitlements.FEATURE_MIN_TIER["tick_minute_k"] is Tier.PREMIUM
