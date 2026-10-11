"""Secret settings are masked everywhere a settings object might be printed or logged."""

from __future__ import annotations

from pydantic import SecretStr

from config.settings import Settings, reveal

_SECRET_FIELDS = [
    name for name, field in Settings.model_fields.items() if "SecretStr" in str(field.annotation)
]


def test_every_credential_setting_is_a_secret_str() -> None:
    # A name that looks like a credential must not be a plain string.
    suspicious = [
        name
        for name, field in Settings.model_fields.items()
        if any(
            hint in name
            for hint in ("api_key", "secret", "access_token", "bot_token", "private_key")
        )
        and "str" in str(field.annotation)  # a text value (not a lifetime in minutes)
        and "SecretStr" not in str(field.annotation)
        and "prefix" not in name
    ]
    assert suspicious == [], f"credential-looking settings stored as plain strings: {suspicious}"


def test_a_secret_never_appears_in_the_settings_repr() -> None:
    settings = Settings(jwt_secret_key="super-secret-jwt", paddle_api_key="pk-live-123")

    shown = repr(settings) + str(settings) + str(settings.model_dump())

    assert "super-secret-jwt" not in shown
    assert "pk-live-123" not in shown


def test_reveal_returns_the_plain_value_or_none() -> None:
    assert reveal(SecretStr("abc")) == "abc"
    assert reveal(None) is None
