"""Opt-in live verification of the real (non-stub) voice providers. Never run in the default
suite — `FasterWhisperTranscriber` downloads/loads a real model file, `EdgeTTSSpeaker` hits a
real Microsoft Edge endpoint, and `OpenAITTSSpeaker` hits the real (billed) OpenAI Audio Speech
API.

Run manually:
    uv run pytest -m network tests/voice/test_providers_network.py
"""

from __future__ import annotations

import pytest

from config.settings import Settings
from voice.providers import EdgeTTSSpeaker, FasterWhisperTranscriber, OpenAITTSSpeaker

pytestmark = pytest.mark.network


async def test_edge_tts_speaker_produces_mp3_audio() -> None:
    speaker = EdgeTTSSpeaker()
    audio = await speaker.speak("台積電今日股價持平，此為非投資建議。", language="zh-TW")
    assert audio.content_type == "audio/mpeg"
    assert audio.spoken_text == "台積電今日股價持平，此為非投資建議。"
    # A real MP3 stream — a few KB minimum for a short sentence, and an MP3-ish leading byte
    # (either an ID3 tag or an MPEG frame sync), not just "some bytes came back".
    assert len(audio.audio) > 1000
    assert audio.audio[:3] == b"ID3" or audio.audio[0] == 0xFF


async def test_edge_tts_speaker_english_voice() -> None:
    speaker = EdgeTTSSpeaker()
    audio = await speaker.speak("This is not investment advice.", language="en-US")
    assert audio.content_type == "audio/mpeg"
    assert len(audio.audio) > 1000


async def test_faster_whisper_transcribes_synthesized_speech() -> None:
    """Round-trips through the real `EdgeTTSSpeaker` to get real speech audio (no fixture file
    checked in), then feeds it to the real `FasterWhisperTranscriber` — proves the temp-file
    hand-off and segment/confidence mapping against actual model output, not a mock."""
    speaker = EdgeTTSSpeaker()
    spoken_text = "台積電"
    audio = await speaker.speak(spoken_text, language="zh-TW")

    transcriber = FasterWhisperTranscriber(model_size="medium")
    transcript = await transcriber.transcribe(audio.audio, language="zh-TW")

    assert transcript.language == "zh-TW"
    assert transcript.text
    assert transcript.segments
    for segment in transcript.segments:
        assert 0.0 <= segment.confidence <= 1.0


async def test_openai_tts_speaker_produces_mp3_audio() -> None:
    """Live smoke test against the real OpenAI Audio Speech API — billed per character, hence
    `network`-marked and never in the default suite. Reads `OPENAI_API_KEY` from the environment
    (via `Settings`) without ever printing/logging its value."""
    settings = Settings()
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY not configured — skipping live OpenAI TTS smoke test.")
    speaker = OpenAITTSSpeaker(api_key=settings.openai_api_key)
    audio = await speaker.speak("台積電今日股價持平，此為非投資建議。", language="zh-TW")
    assert audio.content_type == "audio/mpeg"
    assert audio.spoken_text == "台積電今日股價持平，此為非投資建議。"
    # A real MP3 stream — a few KB minimum for a short sentence, and an MP3-ish leading byte
    # (either an ID3 tag or an MPEG frame sync), not just "some bytes came back".
    assert len(audio.audio) > 1000
    assert audio.audio[:3] == b"ID3" or audio.audio[0] == 0xFF
