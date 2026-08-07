"""JWT access tokens + refresh-token rotation & reuse detection."""

from __future__ import annotations

import pytest

from accounts.auth import (
    RefreshTokenReuseError,
    SqlAlchemySessionStore,
    decode_access_token,
    issue_access_token,
)
from config.settings import Settings
from tests.accounts.conftest import make_user


def _settings() -> Settings:
    return Settings(jwt_secret_key="test-secret-key-do-not-use-in-prod-0123456789abcdef")


def test_access_token_roundtrip() -> None:
    settings = _settings()
    token = issue_access_token(user_id="u1", settings=settings)
    assert decode_access_token(token, settings=settings) == "u1"


async def test_refresh_rotation_invalidates_prior(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemySessionStore(db_session, _settings())
    _sid, raw1 = await store.create(user_id=user_id)
    _sid2, raw2 = await store.rotate(raw_token=raw1)
    assert raw2 != raw1
    # The new token rotates again fine.
    _sid3, raw3 = await store.rotate(raw_token=raw2)
    assert raw3 not in (raw1, raw2)


async def test_reuse_of_rotated_token_revokes_family(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemySessionStore(db_session, _settings())
    _sid, raw1 = await store.create(user_id=user_id)
    _sid2, raw2 = await store.rotate(raw_token=raw1)
    # Present the rotated-out token again → reuse detected.
    with pytest.raises(RefreshTokenReuseError):
        await store.rotate(raw_token=raw1)
    # The whole family (including the current live token) is now revoked.
    with pytest.raises(RefreshTokenReuseError):
        await store.rotate(raw_token=raw2)
