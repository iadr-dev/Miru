"""`attachments.extractor` tests — `markitdown` itself is stubbed via `sys.modules` so the
default (non-network) suite never requires the real `markitdown[docx,pptx,xlsx,pdf]` extra or
does real document parsing, mirroring `voice/test_providers_network.py`'s split (a real-library
smoke test is `network`-marked, opt-in only)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from attachments.extractor import AttachmentExtractionError, extract_text


class _FakeStreamInfo:
    def __init__(self, *, mimetype: str | None = None, filename: str | None = None) -> None:
        self.mimetype = mimetype
        self.filename = filename


class _FakeResult:
    def __init__(self, markdown: str) -> None:
        self.markdown = markdown


class _FakeMarkItDown:
    def __init__(self, *, enable_plugins: bool = False) -> None:
        self.enable_plugins = enable_plugins

    def convert(self, _stream: Any, *, stream_info: _FakeStreamInfo) -> _FakeResult:
        return _FakeResult(f"# converted: {stream_info.filename}")


class _FailingMarkItDown(_FakeMarkItDown):
    def convert(self, _stream: Any, *, stream_info: _FakeStreamInfo) -> _FakeResult:
        raise ValueError("corrupt document")


def _install_fake_markitdown(monkeypatch: pytest.MonkeyPatch, markitdown_cls: type) -> None:
    fake_module = types.ModuleType("markitdown")
    fake_module.MarkItDown = markitdown_cls  # type: ignore[attr-defined]
    fake_stream_info_module = types.ModuleType("markitdown._stream_info")
    fake_stream_info_module.StreamInfo = _FakeStreamInfo  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "markitdown", fake_module)
    monkeypatch.setitem(sys.modules, "markitdown._stream_info", fake_stream_info_module)


async def test_extract_text_returns_markdown(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_markitdown(monkeypatch, _FakeMarkItDown)

    text = await extract_text(b"fake bytes", filename="report.pdf", media_type="application/pdf")

    assert text == "# converted: report.pdf"


async def test_extract_text_wraps_markitdown_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_markitdown(monkeypatch, _FailingMarkItDown)

    with pytest.raises(AttachmentExtractionError):
        await extract_text(b"corrupt", filename="bad.docx", media_type="application/vnd.x")
