"""`SecretStore` — encrypted-at-rest BYOK / channel-token storage (`user_secrets`).

Every value is Fernet-encrypted (`accounts.crypto.SecretCipher`) before it touches the DB, and
decrypted only transiently at call time. The plaintext secret is NEVER passed to structlog —
log lines carry only `user_id`/`kind`/`provider`, never the value or the ciphertext. A removed
key simply makes that provider's BYOK lookup return `None`; the caller falls back to the
platform key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.crypto import SecretCipher
from accounts.models import SecretKind
from db.models import UserSecretRow

logger = structlog.get_logger(__name__)

_BULLET = chr(0x2022)
_HINT_MIN_KEY_LENGTH = 20  # shorter keys show only their tail


def key_hint(plaintext: str) -> str:
    """A masked preview of a key: the first 6 and last 4 characters around bullets (only the last 4
    for a short key), e.g. `sk-ant••••••••a9f3`. Reveals a small fixed slice, never the key."""
    key = plaintext.strip()
    head = key[:6] if len(key) >= _HINT_MIN_KEY_LENGTH else ""
    return f"{head}{_BULLET * 8}{key[-4:]}"


@runtime_checkable
class SecretStore(Protocol):
    async def put(
        self, *, user_id: str, kind: SecretKind, provider: str, plaintext: str
    ) -> None: ...

    async def get(self, *, user_id: str, kind: SecretKind, provider: str) -> str | None: ...

    async def delete(self, *, user_id: str, kind: SecretKind, provider: str) -> bool: ...

    async def has_llm_key(self, *, user_id: str) -> bool: ...

    async def llm_providers(self, *, user_id: str) -> list[str]: ...

    async def llm_key_hints(self, *, user_id: str) -> dict[str, str | None]: ...


class SqlAlchemySecretStore:
    def __init__(self, session: AsyncSession, cipher: SecretCipher) -> None:
        self._session = session
        self._cipher = cipher

    async def put(self, *, user_id: str, kind: SecretKind, provider: str, plaintext: str) -> None:
        ciphertext = self._cipher.encrypt(plaintext)
        result = await self._session.execute(
            select(UserSecretRow).where(
                UserSecretRow.user_id == user_id,
                UserSecretRow.kind == kind.value,
                UserSecretRow.provider == provider,
            )
        )
        row = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if row is None:
            row = UserSecretRow(
                id=str(uuid.uuid4()),
                user_id=user_id,
                kind=kind.value,
                provider=provider,
                ciphertext=ciphertext,
                hint=key_hint(plaintext),
                created_at=now,
                updated_at=now,
            )
            self._session.add(row)
        else:
            row.ciphertext = ciphertext
            row.hint = key_hint(plaintext)
            row.updated_at = now
        await self._session.flush()
        # Never log the plaintext or ciphertext — only the non-secret coordinates.
        logger.info("secret_stored", user_id=user_id, kind=kind.value, provider=provider)

    async def get(self, *, user_id: str, kind: SecretKind, provider: str) -> str | None:
        result = await self._session.execute(
            select(UserSecretRow).where(
                UserSecretRow.user_id == user_id,
                UserSecretRow.kind == kind.value,
                UserSecretRow.provider == provider,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        return self._cipher.decrypt(row.ciphertext)

    async def delete(self, *, user_id: str, kind: SecretKind, provider: str) -> bool:
        result = await self._session.execute(
            select(UserSecretRow).where(
                UserSecretRow.user_id == user_id,
                UserSecretRow.kind == kind.value,
                UserSecretRow.provider == provider,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        logger.info("secret_deleted", user_id=user_id, kind=kind.value, provider=provider)
        return True

    async def llm_providers(self, *, user_id: str) -> list[str]:
        """The providers the user has saved an LLM key for (names only - never the keys)."""
        result = await self._session.execute(
            select(UserSecretRow.provider)
            .where(
                UserSecretRow.user_id == user_id,
                UserSecretRow.kind == SecretKind.LLM_KEY.value,
            )
            .order_by(UserSecretRow.provider)
        )
        return [row[0] for row in result.all()]

    async def llm_key_hints(self, *, user_id: str) -> dict[str, str | None]:
        """provider -> masked preview for each saved LLM key (None for keys saved before previews
        existed). Never the key."""
        result = await self._session.execute(
            select(UserSecretRow.provider, UserSecretRow.hint)
            .where(
                UserSecretRow.user_id == user_id,
                UserSecretRow.kind == SecretKind.LLM_KEY.value,
            )
            .order_by(UserSecretRow.provider)
        )
        return {row[0]: row[1] for row in result.all()}

    async def has_llm_key(self, *, user_id: str) -> bool:
        """Whether the user has any BYOK LLM key — the flag that lifts the chat limit."""
        result = await self._session.execute(
            select(UserSecretRow.id).where(
                UserSecretRow.user_id == user_id,
                UserSecretRow.kind == SecretKind.LLM_KEY.value,
            )
        )
        return result.first() is not None


__all__ = ["SecretStore", "SqlAlchemySecretStore", "key_hint"]
