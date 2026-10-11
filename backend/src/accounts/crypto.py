"""Envelope encryption for secrets at rest (BYOK LLM keys, FinMind/channel tokens).

A thin wrapper over `cryptography`'s Fernet (AES-128-CBC + HMAC authenticated encryption) with
**multi-key decrypt** for rotation: the first configured key encrypts, all configured keys are
tried on decrypt. The plaintext secret is only ever held transiently in-process at call time —
it is never logged, never journaled, never returned to the client, and never written anywhere
but `user_secrets.ciphertext`.

Key material comes from config (`SECRET_ENCRYPTION_KEYS`, comma-separated urlsafe-base64
32-byte Fernet keys) — never hardcoded. With no key configured the encryptor refuses to
operate rather than degrading to plaintext.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from config import get_settings
from config.settings import Settings, reveal


class SecretEncryptionNotConfiguredError(RuntimeError):
    """Raised when no `SECRET_ENCRYPTION_KEYS` is configured — the store fails loud rather than
    persisting a BYOK key in plaintext."""


class SecretDecryptionError(RuntimeError):
    """Raised when ciphertext cannot be decrypted by any configured key (wrong/rotated-out key,
    or corruption). Never carries the ciphertext or key in its message."""


def generate_key() -> str:
    """A fresh urlsafe-base64 Fernet key (for ops/tests to populate config). Not used at
    runtime — runtime keys come from config only."""
    return Fernet.generate_key().decode("ascii")


class SecretCipher:
    """Encrypt/decrypt secret bytes. Constructed from config; holds only key material derived
    from `SECRET_ENCRYPTION_KEYS`, never a plaintext secret."""

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise SecretEncryptionNotConfiguredError(
                "SECRET_ENCRYPTION_KEYS is not configured — refusing to handle secrets"
            )
        self._fernet = MultiFernet([Fernet(key.encode("ascii")) for key in keys])

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SecretCipher:
        settings = settings or get_settings()
        raw = reveal(settings.secret_encryption_keys)
        keys = [part.strip() for part in raw.split(",")] if raw else []
        keys = [key for key in keys if key]
        return cls(keys)

    def encrypt(self, plaintext: str) -> bytes:
        return self._fernet.encrypt(plaintext.encode("utf-8"))

    def decrypt(self, ciphertext: bytes) -> str:
        try:
            return self._fernet.decrypt(ciphertext).decode("utf-8")
        except InvalidToken as exc:
            raise SecretDecryptionError(
                "secret could not be decrypted with any configured key"
            ) from exc


__all__ = [
    "SecretCipher",
    "SecretDecryptionError",
    "SecretEncryptionNotConfiguredError",
    "generate_key",
]
