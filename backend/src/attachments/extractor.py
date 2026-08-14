"""Office/PDF/etc. → Markdown text extraction for chat attachments, via `markitdown`
(Microsoft's document-to-Markdown converter).

`markitdown` does its own file I/O + parsing synchronously (verified by inspecting the
installed package: `MarkItDown.convert_stream` is a plain `def`, not `async def`, and its
Office/PDF converters shell out to synchronous libraries like `python-docx`/`pdfminer`) — the
same "blocking SDK" category as `faster-whisper`/FinMind/yfinance elsewhere in this codebase,
so the call is wrapped in `anyio.to_thread.run_sync` here, mirroring
`voice.providers.FasterWhisperTranscriber`'s exact idiom, never blocking the event loop.

The `markitdown` package is an optional dependency (the `attachments` extras group in
`pyproject.toml`, mirroring `voice`'s `faster-whisper`/`edge-tts`) — imported lazily inside
`extract_text`, not at module load time, so importing this module never requires a deployment
that doesn't accept document attachments to install it.
"""

from __future__ import annotations

import io

import anyio.to_thread
import structlog

logger = structlog.get_logger(__name__)


class AttachmentExtractionError(RuntimeError):
    """Raised when `markitdown` fails to convert a document (corrupt file, unsupported
    sub-format, etc.) — the caller surfaces this as a per-attachment failure, never a crash of
    the whole turn."""


def _convert_blocking(data: bytes, filename: str, media_type: str) -> str:
    """The actual blocking `markitdown` call — imported lazily (see module docstring) and run
    only inside `anyio.to_thread.run_sync`, never directly on the event loop."""
    from markitdown import MarkItDown  # noqa: PLC0415 — lazy import, optional extra
    from markitdown._stream_info import StreamInfo  # noqa: PLC0415 — lazy import, optional extra

    converter = MarkItDown(enable_plugins=False)
    stream_info = StreamInfo(mimetype=media_type, filename=filename)
    try:
        result = converter.convert(io.BytesIO(data), stream_info=stream_info)
    except Exception as exc:  # markitdown raises its own exception hierarchy per-converter —
        # collapsed to one typed error here rather than leaking a third-party exception type
        # up through `attachments/` into `api/`.
        raise AttachmentExtractionError(
            f"markitdown failed to convert {filename!r}: {exc}"
        ) from exc
    return result.markdown


async def extract_text(data: bytes, *, filename: str, media_type: str) -> str:
    """Convert a document attachment's raw bytes to Markdown text, off the event loop.

    Never called for image attachments (images go to the LLM as vision input directly — see
    `api/routers/agent.py`'s attachment-processing docstring); this is the document-only path.
    """
    return await anyio.to_thread.run_sync(_convert_blocking, data, filename, media_type)


__all__ = ["AttachmentExtractionError", "extract_text"]
