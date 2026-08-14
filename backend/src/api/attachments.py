"""Request-level chat-attachment glue: `AttachmentInput` (the wire shape `AgentQueryRequest`/
`RegenerateRequest`/`EditTurnRequest` all share) plus `process_attachments`, which turns a
request's raw base64 attachments into what the turn-driver/orchestrator/store each need —
`llm.base.ImagePart`s for vision input, augmented prompt text for extracted document content,
and `conversation.models.AttachmentBlock`s to persist on the user's turn.

Lives in `api/` (not `attachments/`) because it is the one place base64-decoding, the size
cap, `ApiError` construction, and `conversation.models` block-building all need to compose —
`attachments/` itself stays a leaf with no `api`/`conversation` dependency (see `attachments/
processing.py`'s own docstring)."""

from __future__ import annotations

import base64

import structlog
from fastapi import status
from pydantic import BaseModel, ConfigDict, Field

from api.errors import ApiError
from attachments.extractor import AttachmentExtractionError
from attachments.processing import process_attachment, render_attachment_context
from config.settings import Settings
from conversation.models import AttachmentBlock, MessageBlockContent, MessageBlockType
from llm.base import ImagePart

logger = structlog.get_logger(__name__)


class AttachmentInput(BaseModel):
    """One chat attachment as sent by the client — the whole file inline as base64, mirroring
    `voice.py`'s `audio_base64` idiom (base64-in-JSON, not multipart)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1)


def _decode(attachment: AttachmentInput, *, max_bytes: int) -> bytes:
    try:
        data = base64.b64decode(attachment.data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ApiError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="invalid_request",
            message=f"attachment {attachment.filename!r}: data_base64 is not valid base64.",
            retryable=False,
        ) from exc
    if len(data) > max_bytes:
        raise ApiError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            code="attachment_too_large",
            message=f"attachment {attachment.filename!r} exceeds the maximum size.",
            retryable=False,
        )
    return data


async def process_attachments(
    attachments: tuple[AttachmentInput, ...],
    *,
    message: str,
    settings: Settings,
) -> tuple[str, tuple[ImagePart, ...], list[tuple[MessageBlockType, MessageBlockContent]]]:
    """Decode + process every attachment on this request, returning:

    - `augmented_message`: `message` with each document attachment's extracted text appended
      as a clearly-labeled, delimited block (see `attachments.processing.
      render_attachment_context`) — the same "extra context alongside the user's message"
      mechanism this codebase has no other name for; there is no separate context-assembly
      seam in `context/`/`agent/prompts/` for per-turn ad-hoc text, so it is folded directly
      into the text sent to the model, exactly like a user pasting the text themselves would.
    - `images`: every image attachment as an `ImagePart`, passed straight to `Orchestrator.run`.
    - `blocks`: one `(ATTACHMENT, AttachmentBlock)` tuple per attachment, in request order, for
      the caller to append onto the user turn's persisted blocks (metadata + extracted text
      only — never the raw bytes, see `AttachmentBlock`'s own docstring).

    A single bad attachment (oversized, invalid base64, or a document `markitdown` cannot
    parse) fails the whole request with a 4xx `ApiError` rather than silently dropping it —
    the user explicitly attached it, so a silent drop would be a worse surprise than an error.
    """
    augmented = message
    images: list[ImagePart] = []
    blocks: list[tuple[MessageBlockType, MessageBlockContent]] = []
    for attachment in attachments:
        data = _decode(attachment, max_bytes=settings.max_attachment_size_bytes)
        try:
            processed = await process_attachment(
                data, filename=attachment.filename, media_type=attachment.media_type
            )
        except AttachmentExtractionError as exc:
            logger.warning(
                "attachment_extraction_failed",
                filename=attachment.filename,
                media_type=attachment.media_type,
                error=str(exc),
            )
            raise ApiError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="attachment_extraction_failed",
                message=f"Could not read attachment {attachment.filename!r}.",
                retryable=False,
            ) from exc
        if processed.image is not None:
            images.append(processed.image)
        if processed.extracted_text is not None:
            context_block = render_attachment_context(attachment.filename, processed.extracted_text)
            augmented = f"{augmented}\n\n{context_block}"
        blocks.append(
            (
                MessageBlockType.ATTACHMENT,
                AttachmentBlock(
                    filename=processed.filename,
                    media_type=processed.media_type,
                    is_image=processed.is_image,
                    extracted_text=processed.extracted_text,
                    size_bytes=processed.size_bytes,
                ),
            )
        )
    return augmented, tuple(images), blocks


__all__ = ["AttachmentInput", "process_attachments"]
