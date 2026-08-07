"""Voice transcribe/speak: transcript-only (no turn creation), ticker normalization,
vocabulary-biasing hints, and the spoken-caveat governance rule. Stub providers only — no
network in the default suite."""

from __future__ import annotations

import pytest

from voice.providers import StubSpeaker, StubTranscriber
from voice.service import build_hints, ensure_caveated_for_speech, speak, transcribe


async def test_transcribe_returns_transcript_only() -> None:
    transcriber = StubTranscriber()
    result = await transcribe("台積電基本面如何".encode(), transcriber, language="zh-TW")
    assert result.text == "台積電基本面如何"
    assert result.language == "zh-TW"
    # A Transcript, not a Turn — the type itself proves no turn was created.
    assert type(result).__name__ == "Transcript"


async def test_spoken_zh_ticker_normalized_against_universe() -> None:
    transcriber = StubTranscriber()
    result = await transcribe(
        "二三三零 的走勢".encode(), transcriber, symbol_universe=frozenset({"2330"})
    )
    assert "2330" in result.text
    assert "二三三零" not in result.text


async def test_unknown_spoken_number_not_substituted() -> None:
    transcriber = StubTranscriber()
    # 9999 not in the universe → left as spoken digits, never invented into a symbol.
    result = await transcribe(
        "九九九九 這檔".encode(), transcriber, symbol_universe=frozenset({"2330"})
    )
    assert "9999" not in result.text


def test_hints_prepend_watchlist_then_lexicon() -> None:
    hints = build_hints(("2330", "台積電"))
    assert hints[0] == "2330"
    assert "加權指數" in hints  # core lexicon appended


_CAVEAT_MARKERS = ("not", "非投資建議", "real-time", "recommendation")


def test_speak_enforces_caveat() -> None:
    # Analysis text with no caveat gets one appended before it is spoken.
    caveated = ensure_caveated_for_speech("台積電今日上漲，成交量放大。")
    lowered = caveated.lower()
    assert any(m in lowered or m in caveated for m in _CAVEAT_MARKERS)


@pytest.mark.governance
async def test_spoken_text_never_drops_caveat() -> None:
    speaker = StubSpeaker()
    audio = await speak("加權指數收紅。", speaker)
    # The stub echoes spoken_text; assert the caveat survived into what would be synthesized.
    assert any(m in audio.spoken_text.lower() or m in audio.spoken_text for m in _CAVEAT_MARKERS)


@pytest.mark.governance
async def test_spoken_text_redacts_trading_language() -> None:
    speaker = StubSpeaker()
    audio = await speak("You should buy now.", speaker)
    assert "buy now" not in audio.spoken_text.lower()
