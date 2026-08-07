"""BYOK secret storage: encrypted at rest, decryptable, never logged in plaintext."""

from __future__ import annotations

import structlog
from sqlalchemy import select

from accounts.crypto import SecretCipher, generate_key
from accounts.models import SecretKind
from accounts.secrets import SqlAlchemySecretStore
from db.models import UserSecretRow
from tests.accounts.conftest import make_user


def _cipher() -> SecretCipher:
    return SecretCipher([generate_key()])


async def test_byok_key_stored_encrypted_and_retrievable(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemySecretStore(db_session, _cipher())
    await store.put(
        user_id=user_id, kind=SecretKind.LLM_KEY, provider="anthropic", plaintext="sk-secret-123"
    )
    # Round-trips through decryption.
    got = await store.get(user_id=user_id, kind=SecretKind.LLM_KEY, provider="anthropic")
    assert got == "sk-secret-123"
    # Stored ciphertext is NOT the plaintext.
    row = (
        await db_session.execute(select(UserSecretRow).where(UserSecretRow.user_id == user_id))
    ).scalar_one()
    assert b"sk-secret-123" not in row.ciphertext
    assert isinstance(row.ciphertext, bytes)


async def test_has_llm_key_and_delete(db_session) -> None:
    user_id = await make_user(db_session)
    store = SqlAlchemySecretStore(db_session, _cipher())
    assert not await store.has_llm_key(user_id=user_id)
    await store.put(user_id=user_id, kind=SecretKind.LLM_KEY, provider="openai", plaintext="sk-x")
    assert await store.has_llm_key(user_id=user_id)
    assert await store.delete(user_id=user_id, kind=SecretKind.LLM_KEY, provider="openai")
    assert not await store.has_llm_key(user_id=user_id)
    assert await store.get(user_id=user_id, kind=SecretKind.LLM_KEY, provider="openai") is None


async def test_plaintext_secret_never_logged(db_session) -> None:
    """Assert on the actual structlog calls: the plaintext must never appear in any log
    event's args/kwargs."""
    captured: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _capture(_logger: object, _method: str, event_dict: dict[str, object]) -> dict[str, object]:
        captured.append(((), dict(event_dict)))
        return event_dict

    structlog.configure(processors=[_capture, structlog.processors.KeyValueRenderer()])
    try:
        user_id = await make_user(db_session)
        store = SqlAlchemySecretStore(db_session, _cipher())
        await store.put(
            user_id=user_id,
            kind=SecretKind.LLM_KEY,
            provider="anthropic",
            plaintext="sk-TOP-SECRET",
        )
    finally:
        structlog.reset_defaults()

    for _args, kwargs in captured:
        for value in kwargs.values():
            assert "sk-TOP-SECRET" not in str(value)
    assert captured  # a log line was in fact emitted
