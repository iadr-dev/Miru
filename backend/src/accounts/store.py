"""`UserStore` — the SQLAlchemy-backed identity + profile layer (`users` + `oauth_accounts`).

Same per-request-session convention as `memory.store` / `conversation.store`: the caller owns
the session/transaction boundary. Identity resolution at OAuth callback is the `upsert_via_oauth`
method: known provider identity → that user; new identity → link on verified matching email, else
a new user. Explicit `link_provider` handles the logged-in Settings "link account" case.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import OAuthAccount, OAuthProvider, User
from db.models import OAuthAccountRow, UserRow
from schemas.enums import Tier

logger = structlog.get_logger(__name__)


class LastLoginMethodError(RuntimeError):
    """Unlinking the only remaining login method would orphan the account — refused."""


class UserNotFoundError(LookupError):
    pass


@runtime_checkable
class UserStore(Protocol):
    async def get(self, *, user_id: str) -> User: ...

    async def upsert_via_oauth(  # noqa: PLR0913 — one field per identity dimension the callback carries
        self,
        *,
        provider: OAuthProvider,
        provider_account_id: str,
        email: str | None,
        email_verified: bool,
        display_name: str = "",
        picture_url: str | None = None,
    ) -> User: ...

    async def link_provider(
        self,
        *,
        user_id: str,
        provider: OAuthProvider,
        provider_account_id: str,
        email_at_provider: str | None = None,
    ) -> OAuthAccount: ...

    async def set_tier(self, *, user_id: str, tier: Tier) -> User: ...

    async def unlink_provider(self, *, user_id: str, provider: OAuthProvider) -> None: ...

    async def list_providers(self, *, user_id: str) -> list[OAuthAccount]: ...

    async def update_profile(  # noqa: PLR0913 — one field per settable profile attr
        self,
        *,
        user_id: str,
        display_name: str | None = None,
        birthday: str | None = None,
        phone_country_code: str | None = None,
        phone_number: str | None = None,
        nationality: str | None = None,
        personality_id: str | None = None,
    ) -> User: ...


def _row_to_user(row: UserRow) -> User:
    return User(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        picture_url=row.picture_url,
        birthday=row.birthday,
        phone_country_code=row.phone_country_code,
        phone_number=row.phone_number,
        nationality=row.nationality,
        tier=Tier(row.tier),
        personality_id=row.personality_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_oauth(row: OAuthAccountRow) -> OAuthAccount:
    return OAuthAccount(
        id=row.id,
        user_id=row.user_id,
        provider=OAuthProvider(row.provider),
        provider_account_id=row.provider_account_id,
        email_at_provider=row.email_at_provider,
        linked_at=row.linked_at,
    )


class SqlAlchemyUserStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get_row(self, user_id: str) -> UserRow:
        result = await self._session.execute(
            select(UserRow).where(UserRow.id == user_id, UserRow.deleted_at.is_(None))
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise UserNotFoundError(user_id)
        return row

    async def get(self, *, user_id: str) -> User:
        return _row_to_user(await self._get_row(user_id))

    async def upsert_via_oauth(  # noqa: PLR0913 — one field per identity dimension the callback carries
        self,
        *,
        provider: OAuthProvider,
        provider_account_id: str,
        email: str | None,
        email_verified: bool,
        display_name: str = "",
        picture_url: str | None = None,
    ) -> User:
        # 1. Known provider identity → that user. An identity linked to several accounts signs
        # into the one that linked it first.
        existing_link = await self._session.execute(
            select(OAuthAccountRow)
            .where(
                OAuthAccountRow.provider == provider.value,
                OAuthAccountRow.provider_account_id == provider_account_id,
            )
            .order_by(OAuthAccountRow.linked_at, OAuthAccountRow.id)
            .limit(1)
        )
        link_row = existing_link.scalar_one_or_none()
        if link_row is not None:
            logger.info(
                "oauth_login_known_identity", provider=provider.value, user_id=link_row.user_id
            )
            return _row_to_user(await self._get_row(link_row.user_id))

        # 2. New identity, logged-out: auto-link only on a *verified* matching email.
        if email is not None and email_verified:
            matched = await self._session.execute(
                select(UserRow).where(UserRow.email == email, UserRow.deleted_at.is_(None))
            )
            matched_user = matched.scalar_one_or_none()
            if matched_user is not None:
                await self._add_link(
                    user_id=matched_user.id,
                    provider=provider,
                    provider_account_id=provider_account_id,
                    email_at_provider=email,
                )
                logger.info(
                    "oauth_autolinked_by_email", provider=provider.value, user_id=matched_user.id
                )
                return _row_to_user(matched_user)

        # 3. Otherwise create a new user + its first provider link. An unverified email is never
        # persisted on the new row — it can't be trusted as this identity's own email yet, and
        # storing it would risk colliding with `uq_users_email` (partial-unique-where-not-null)
        # against whichever account actually owns that address.
        now = datetime.now(UTC)
        user_row = UserRow(
            id=str(uuid.uuid4()),
            email=email if email_verified else None,
            display_name=display_name,
            picture_url=picture_url,
            tier=Tier.FREE.value,
            personality_id="steady",
            created_at=now,
            updated_at=now,
        )
        self._session.add(user_row)
        await self._session.flush()
        await self._add_link(
            user_id=user_row.id,
            provider=provider,
            provider_account_id=provider_account_id,
            email_at_provider=email,
        )
        logger.info("user_created_via_oauth", provider=provider.value, user_id=user_row.id)
        return _row_to_user(user_row)

    async def link_provider(
        self,
        *,
        user_id: str,
        provider: OAuthProvider,
        provider_account_id: str,
        email_at_provider: str | None = None,
    ) -> OAuthAccount:
        existing = await self._session.execute(
            select(OAuthAccountRow).where(
                OAuthAccountRow.user_id == user_id,
                OAuthAccountRow.provider == provider.value,
                OAuthAccountRow.provider_account_id == provider_account_id,
            )
        )
        existing_row = existing.scalar_one_or_none()
        if existing_row is not None:
            return _row_to_oauth(existing_row)
        row = await self._add_link(
            user_id=user_id,
            provider=provider,
            provider_account_id=provider_account_id,
            email_at_provider=email_at_provider,
        )
        return _row_to_oauth(row)

    async def set_tier(self, *, user_id: str, tier: Tier) -> User:
        """Write the derived tier cache. The billing webhook is the ONLY caller of this — the tier
        is never set from a client-asserted value. Kept deliberately narrow (tier only) so no
        other path can smuggle a tier change in through the general profile update."""
        row = await self._get_row(user_id)
        row.tier = tier.value
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("user_tier_set", user_id=user_id, tier=tier.value)
        return _row_to_user(row)

    async def unlink_provider(self, *, user_id: str, provider: OAuthProvider) -> None:
        links = await self.list_providers(user_id=user_id)
        if len(links) <= 1:
            raise LastLoginMethodError("cannot unlink the only remaining login method")
        result = await self._session.execute(
            select(OAuthAccountRow).where(
                OAuthAccountRow.user_id == user_id, OAuthAccountRow.provider == provider.value
            )
        )
        row = result.scalar_one_or_none()
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()
            logger.info("oauth_unlinked", provider=provider.value, user_id=user_id)

    async def list_providers(self, *, user_id: str) -> list[OAuthAccount]:
        result = await self._session.execute(
            select(OAuthAccountRow).where(OAuthAccountRow.user_id == user_id)
        )
        return [_row_to_oauth(row) for row in result.scalars().all()]

    async def update_profile(  # noqa: PLR0913 — one field per independently-settable profile attr
        self,
        *,
        user_id: str,
        display_name: str | None = None,
        birthday: str | None = None,
        phone_country_code: str | None = None,
        phone_number: str | None = None,
        nationality: str | None = None,
        personality_id: str | None = None,
    ) -> User:
        """Patch mutable profile fields. Only non-`None` arguments are applied, so a caller can
        clear a field only by sending "" (not omission) — omission always means "leave as-is".
        `tier` is never settable here (billing-webhook-owned)."""
        row = await self._get_row(user_id)
        if display_name is not None:
            row.display_name = display_name
        if birthday is not None:
            row.birthday = birthday
        if phone_country_code is not None:
            row.phone_country_code = phone_country_code
        if phone_number is not None:
            row.phone_number = phone_number
        if nationality is not None:
            row.nationality = nationality
        if personality_id is not None:
            row.personality_id = personality_id
        row.updated_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("user_profile_updated", user_id=user_id)
        return _row_to_user(row)

    async def _add_link(
        self,
        *,
        user_id: str,
        provider: OAuthProvider,
        provider_account_id: str,
        email_at_provider: str | None,
    ) -> OAuthAccountRow:
        row = OAuthAccountRow(
            id=str(uuid.uuid4()),
            user_id=user_id,
            provider=provider.value,
            provider_account_id=provider_account_id,
            email_at_provider=email_at_provider,
            linked_at=datetime.now(UTC),
        )
        self._session.add(row)
        await self._session.flush()
        return row


__all__ = [
    "LastLoginMethodError",
    "SqlAlchemyUserStore",
    "UserNotFoundError",
    "UserStore",
]
