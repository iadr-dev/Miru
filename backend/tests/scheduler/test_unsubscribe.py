"""Signed unsubscribe links: unforgeable, tied to the account's current email, HTTPS only."""

from __future__ import annotations

from scheduler.unsubscribe import make_token, split_token, unsubscribe_url, verify_token

_SECRET = "server-secret"


def test_a_token_verifies_for_its_own_user_and_email_only() -> None:
    token = make_token(_SECRET, "user-1", "a@example.test")
    assert verify_token(_SECRET, token, "user-1", "a@example.test")
    assert verify_token(_SECRET, token, "user-1", "A@Example.test")  # case does not matter
    assert not verify_token(_SECRET, token, "user-2", "a@example.test")  # someone else's account
    assert not verify_token(_SECRET, token, "user-1", "b@example.test")  # the email changed
    assert not verify_token("other-secret", token, "user-1", "a@example.test")


def test_a_token_naming_another_user_cannot_be_reused() -> None:
    mine = make_token(_SECRET, "user-1", "a@example.test")
    _, signature = split_token(mine) or ("", "")
    forged = f"user-2.{signature}"
    assert not verify_token(_SECRET, forged, "user-2", "a@example.test")
    assert not verify_token(_SECRET, forged, "user-2", "b@example.test")


def test_malformed_tokens_are_rejected_not_raised() -> None:
    for bad in ("", "nodot", ".sig", "user.", "a.b.c"):
        assert not verify_token(_SECRET, bad, "user-1", "a@example.test")


def test_the_link_is_https_only() -> None:
    url = unsubscribe_url("https://miru.example/", _SECRET, "u", "a@example.test")
    assert url is not None
    assert url.startswith("https://miru.example/api/v1/email/unsubscribe?token=u.")
    assert unsubscribe_url("http://localhost:8000", _SECRET, "u", "a@example.test") is None
