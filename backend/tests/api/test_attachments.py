"""`api.attachments` tests — the request-level base64-decode/size-cap/count-cap glue in front
of `attachments.processing`."""

from __future__ import annotations

import base64

import pytest

from api.attachments import AttachmentInput, process_attachments
from api.errors import ApiError
from config.settings import Settings


def _settings() -> Settings:
    # `_env_file=None` so a developer's local `.env` can never leak into this test's expectations.
    return Settings(  # type: ignore[call-arg]
        _env_file=None,  # type: ignore[arg-type]
        jwt_secret_key="test-secret-not-a-real-key-0123456789abcdef",
    )


def _attachment(name: str, data: bytes = b"hello") -> AttachmentInput:
    return AttachmentInput(
        filename=name,
        media_type="text/plain",
        data_base64=base64.b64encode(data).decode("ascii"),
    )


async def test_process_attachments_rejects_too_many_per_request() -> None:
    attachments = tuple(_attachment(f"f{i}.txt") for i in range(11))

    with pytest.raises(ApiError) as exc_info:
        await process_attachments(attachments, message="hi", settings=_settings())

    assert exc_info.value.code == "too_many_attachments"


async def test_process_attachments_accepts_the_max_allowed_count() -> None:
    attachments = tuple(_attachment(f"f{i}.txt") for i in range(10))

    augmented, images, blocks = await process_attachments(
        attachments, message="hi", settings=_settings()
    )

    assert len(blocks) == 10
    assert images == ()
    assert "hi" in augmented
