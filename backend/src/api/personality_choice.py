"""A per-chat 語氣 choice: validate it against the preset registry and say what to store."""

from __future__ import annotations

from api.dependencies import AppState
from api.errors import ApiError

FOLLOW_ACCOUNT = "default"


def personality_patch(state: AppState, choice: str | None) -> tuple[bool, str | None]:
    """`(clear, preset_id)` for a requested choice: nothing asked -> leave it alone; "default" ->
    clear the chat's override so it follows the account setting again; a preset id -> store it.
    An id the registry does not know is a 422, never stored (a stale id would silently fall through
    to the default at resolve time, which would hide the mistake)."""
    if choice is None:
        return False, None
    if choice == FOLLOW_ACCOUNT:
        return True, None
    if choice not in state.personality_registry.known_ids():
        raise ApiError(
            status_code=422,
            code="unknown_personality",
            message=f"Unknown personality preset {choice!r}.",
            retryable=False,
        )
    return False, choice


__all__ = ["FOLLOW_ACCOUNT", "personality_patch"]
