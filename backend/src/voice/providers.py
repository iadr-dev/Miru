"""STT/TTS provider seams — the same config-selected idiom as
`SourceAdapter`/`LLMClient`/`DeliveryChannel`. A concrete provider (self-host whisper, or cloud
Google/Azure/Deepgram) is chosen by config (`VOICE_STT_PROVIDER`/`VOICE_TTS_PROVIDER`), never
hardcoded.

Only the deterministic **stub** providers ship in the default test profile — they perform no
network I/O, keeping the default suite free of live provider calls. Real cloud/self-host
adapters live behind the same Protocols and are selected only when configured; building those
adapters (which do network I/O, so they'd live under `sources/` transport-wise) is deferred
until a provider is wired.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from voice.models import SpeechAudio, Transcript, TranscriptSegment

_LOW_CONFIDENCE_THRESHOLD = 0.6


@runtime_checkable
class Transcriber(Protocol):
    async def transcribe(
        self, audio: bytes, *, language: str = "zh-TW", hints: tuple[str, ...] = ()
    ) -> Transcript: ...


@runtime_checkable
class Speaker(Protocol):
    async def speak(self, ssml_or_text: str, *, language: str = "zh-TW") -> SpeechAudio: ...


class StubTranscriber:
    """Deterministic STT for the default profile — no network. Decodes the audio bytes as UTF-8
    text (tests hand it the expected transcript as bytes), applies `hints` as a marker of
    vocabulary biasing, and reports full confidence unless the payload is empty."""

    def __init__(self, *, confidence: float = 0.99) -> None:
        self._confidence = confidence

    async def transcribe(
        self, audio: bytes, *, language: str = "zh-TW", hints: tuple[str, ...] = ()
    ) -> Transcript:
        del hints  # accepted for Protocol conformance; a real STT adapter biases on this
        text = audio.decode("utf-8", errors="replace").strip()
        confidence = self._confidence if text else 0.0
        return Transcript(
            text=text,
            language=language,
            segments=(TranscriptSegment(text=text, confidence=confidence),) if text else (),
            low_confidence=confidence < _LOW_CONFIDENCE_THRESHOLD,
        )


class StubSpeaker:
    """Deterministic TTS for the default profile — no network. 'Renders' by UTF-8-encoding the
    text; the real adapter would return encoded audio for the same string."""

    def __init__(self, *, content_type: str = "audio/mpeg") -> None:
        self._content_type = content_type

    async def speak(self, ssml_or_text: str, *, language: str = "zh-TW") -> SpeechAudio:
        del language  # accepted for Protocol conformance; a real TTS adapter voices per-locale
        return SpeechAudio(
            audio=ssml_or_text.encode("utf-8"),
            content_type=self._content_type,
            spoken_text=ssml_or_text,
        )


__all__ = ["Speaker", "StubSpeaker", "StubTranscriber", "Transcriber"]
