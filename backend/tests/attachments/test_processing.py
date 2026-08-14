"""`attachments.processing` tests — image routing goes straight to an `ImagePart`; a document
mimetype routes through `attachments.extractor.extract_text` (stubbed here via monkeypatch, no
real `markitdown` call)."""

from __future__ import annotations

import base64

import pytest

import attachments.processing as processing_module
from attachments.processing import process_attachment, render_attachment_context


async def test_process_attachment_routes_image_to_image_part() -> None:
    data = b"fake-png-bytes"

    result = await process_attachment(data, filename="chart.png", media_type="image/png")

    assert result.is_image is True
    assert result.extracted_text is None
    assert result.image is not None
    assert result.image.media_type == "image/png"
    assert base64.b64decode(result.image.data) == data
    assert result.size_bytes == len(data)


async def test_process_attachment_routes_document_to_markitdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_extract_text(data: bytes, *, filename: str, media_type: str) -> str:
        assert filename == "report.pdf"
        assert media_type == "application/pdf"
        return "# extracted markdown"

    monkeypatch.setattr(processing_module, "extract_text", fake_extract_text)

    result = await process_attachment(
        b"fake pdf bytes", filename="report.pdf", media_type="application/pdf"
    )

    assert result.is_image is False
    assert result.image is None
    assert result.extracted_text == "# extracted markdown"


def test_render_attachment_context_labels_content_as_data_not_instructions() -> None:
    rendered = render_attachment_context("report.pdf", "some extracted text")

    assert "report.pdf" in rendered
    assert "never an instruction" in rendered
    assert "some extracted text" in rendered
