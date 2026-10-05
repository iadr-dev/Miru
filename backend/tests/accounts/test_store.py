"""OAuth-upsert identity resolution + account linking."""

from __future__ import annotations

import pytest

from accounts.models import OAuthProvider
from accounts.store import (
    LastLoginMethodError,
    SqlAlchemyUserStore,
)


async def test_known_identity_logs_into_same_user(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    u1 = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-123",
        email="a@x.com",
        email_verified=True,
        display_name="A",
    )
    u2 = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-123",
        email="a@x.com",
        email_verified=True,
    )
    assert u1.id == u2.id


async def test_verified_email_autolinks_new_provider(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    u1 = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-1",
        email="a@x.com",
        email_verified=True,
    )
    # New LINE identity with the same *verified* email → same account.
    u2 = await store.upsert_via_oauth(
        provider=OAuthProvider.LINE,
        provider_account_id="l-1",
        email="a@x.com",
        email_verified=True,
    )
    assert u1.id == u2.id
    providers = {p.provider for p in await store.list_providers(user_id=u1.id)}
    assert providers == {OAuthProvider.GOOGLE, OAuthProvider.LINE}


async def test_unverified_email_does_not_autolink(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    u1 = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-1",
        email="a@x.com",
        email_verified=True,
    )
    u2 = await store.upsert_via_oauth(
        provider=OAuthProvider.LINE,
        provider_account_id="l-1",
        email="a@x.com",
        email_verified=False,
    )
    assert u1.id != u2.id


async def test_line_without_email_creates_distinct_users(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    u1 = await store.upsert_via_oauth(
        provider=OAuthProvider.LINE,
        provider_account_id="l-1",
        email=None,
        email_verified=False,
    )
    u2 = await store.upsert_via_oauth(
        provider=OAuthProvider.LINE,
        provider_account_id="l-2",
        email=None,
        email_verified=False,
    )
    assert u1.id != u2.id


async def test_one_identity_can_be_linked_to_several_accounts(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    u1 = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-1",
        email="a@x.com",
        email_verified=True,
    )
    u2 = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-2",
        email="b@x.com",
        email_verified=True,
    )
    await store.link_provider(
        user_id=u2.id, provider=OAuthProvider.GOOGLE, provider_account_id="g-1"
    )
    # both accounts show it as linked ...
    first = {a.provider_account_id for a in await store.list_providers(user_id=u1.id)}
    second = {a.provider_account_id for a in await store.list_providers(user_id=u2.id)}
    assert first == {"g-1"}
    assert second == {"g-1", "g-2"}
    # ... signing in with it opens the account that linked it first ...
    again = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-1",
        email="a@x.com",
        email_verified=True,
    )
    assert again.id == u1.id
    # ... and linking it twice to the same account changes nothing
    await store.link_provider(
        user_id=u2.id, provider=OAuthProvider.GOOGLE, provider_account_id="g-1"
    )
    assert len(await store.list_providers(user_id=u2.id)) == 2


async def test_cannot_unlink_last_login_method(db_session) -> None:
    store = SqlAlchemyUserStore(db_session)
    u1 = await store.upsert_via_oauth(
        provider=OAuthProvider.GOOGLE,
        provider_account_id="g-1",
        email="a@x.com",
        email_verified=True,
    )
    with pytest.raises(LastLoginMethodError):
        await store.unlink_provider(user_id=u1.id, provider=OAuthProvider.GOOGLE)
