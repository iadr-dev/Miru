"""`Memory`'s own governance validation — refuses credentials/raw payloads/trading language in
`content` regardless of the store layer."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from memory.models import Memory, MemoryScope, MemoryType


def _base_kwargs() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "id": "mem-1",
        "user_id": "user-1",
        "scope": MemoryScope.USER,
        "type": MemoryType.PREFERENCE,
        "key": "verbosity",
        "content": {"level": "concise"},
        "created_at": now,
        "updated_at": now,
    }


def test_valid_memory_constructs_cleanly() -> None:
    memory = Memory.model_validate(_base_kwargs())
    assert memory.key == "verbosity"


@pytest.mark.parametrize(
    "content",
    [
        {"api_key": "sk-something"},
        {"access_token": "abc"},
        {"raw_payload": {"secret": "x"}},
    ],
)
def test_rejects_credential_or_raw_payload_shaped_content(content: dict[str, object]) -> None:
    kwargs = _base_kwargs()
    kwargs["content"] = content
    with pytest.raises(ValidationError):
        Memory.model_validate(kwargs)


def test_rejects_trading_recommendation_language() -> None:
    kwargs = _base_kwargs()
    kwargs["content"] = {"note": "you should buy this stock now"}
    with pytest.raises(ValidationError):
        Memory.model_validate(kwargs)


def test_thread_scope_requires_thread_id() -> None:
    kwargs = _base_kwargs()
    kwargs["scope"] = MemoryScope.THREAD
    kwargs["thread_id"] = None
    with pytest.raises(ValidationError):
        Memory.model_validate(kwargs)
