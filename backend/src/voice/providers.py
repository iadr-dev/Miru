"""STT/TTS provider seams — the same config-selected idiom as
`SourceAdapter`/`LLMClient`/`DeliveryChannel`. A concrete provider (self-host whisper, or cloud
Google/Azure/Deepgram) is chosen by config (`VOICE_STT_PROVIDER`/`VOICE_TTS_PROVIDER`), never
hardcoded.

Only the deterministic **stub** providers run in the default test profile — they perform no
network I/O, keeping the default suite free of live provider calls. `FasterWhisperTranscriber`
(self-host, model-file I/O but no network per request), `EdgeTTSSpeaker` (cloud, real network,
free), and `OpenAITTSSpeaker` (cloud, real network, billed per character via the already-shared
`openai_api_key`) are opt-in real providers behind the same Protocols, selected only when
`voice_stt_provider`/`voice_tts_provider` name them — never constructed unless configured.
`FasterWhisperTranscriber`/`EdgeTTSSpeaker` depend on the optional `voice` extra
(`faster-whisper`, `edge-tts`); `OpenAITTSSpeaker` reuses the `openai` SDK already a base
dependency for `llm/openai_client.py`. All three real providers' imports are deferred into each
provider's methods so importing this module never requires an optional extra unless a real
provider is actually selected.
"""

from __future__ import annotations

import math
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

import anyio.to_thread
import structlog

from voice.models import SpeechAudio, Transcript, TranscriptSegment

if TYPE_CHECKING:
    from faster_whisper import WhisperModel as _WhisperModel
    from faster_whisper.transcribe import Segment as _WhisperSegment
    from faster_whisper.transcribe import TranscriptionInfo as _WhisperTranscriptionInfo

logger = structlog.get_logger(__name__)

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


# faster-whisper's own language vocabulary (ISO 639-1, e.g. "en", "zh") — not the BCP-47
# locale tags (`zh-TW`, `en-US`) this codebase otherwise uses everywhere else (API request
# bodies, `Transcript.language`, `Speaker.speak`'s `language` param). Map the handful this app
# actually offers; anything unrecognized is passed through unmapped so whisper's own
# language-detection decides (rather than the adapter silently mis-guessing).
_WHISPER_LANGUAGE_CODES: dict[str, str] = {
    "zh-TW": "zh",
    "zh-CN": "zh",
    "zh": "zh",
    "en-US": "en",
    "en-GB": "en",
    "en": "en",
}

# medium vs. large-v3: large-v3 is meaningfully more accurate on Mandarin/zh code-switched
# audio, but its latency and VRAM/RAM footprint make it a poor fit for a synchronous
# request/response voice endpoint (this is not a batch transcription job). "medium" is the
# documented default trade — good multilingual (incl. zh) accuracy at a latency the
# `POST /voice/transcribe` request/response cycle can tolerate on CPU. Callers needing
# large-v3-grade accuracy can pass `model_size="large-v3"` explicitly (e.g. via a future
# settings knob); this default is a data choice, not a hardcoded core decision.
_DEFAULT_WHISPER_MODEL_SIZE = "medium"


class FasterWhisperTranscriber:
    """Self-hosted STT via `faster-whisper` (CTranslate2 Whisper). No network call per request —
    the model is downloaded once (huggingface hub cache) and then runs entirely locally, which is
    why this provider is opt-in via `voice_stt_provider=faster_whisper` rather than the default.

    **zh-TW vs. zh-CN caveat (read before assuming this "does" Traditional Chinese):** Whisper's
    language vocabulary only has a single `"zh"` code — it does not expose an independent
    Traditional-vs-Simplified script switch, and its training mix leans Simplified. Passing
    `language="zh-TW"` here only pins the *spoken-language* model path to Mandarin (helping
    latency/accuracy vs. leaving language auto-detected); it does **not** guarantee the emitted
    *script* is Traditional. In practice the model's output script tracks whatever script
    happened to co-occur with the audio's phonetic/acoustic patterns in training, so zh-TW audio
    frequently comes back correctly as Traditional characters, but this is not contractually
    guaranteed by the model and there is no documented flag to force it. If script fidelity ever
    matters more than it does for casual voice-query transcription, a post-pass through a
    Simplified→Traditional converter (e.g. OpenCC) would be the fix — not attempted here since
    the confirm-before-send UX already gives the user a chance to correct the text before it's
    used.
    """

    def __init__(
        self,
        *,
        model_size: str = _DEFAULT_WHISPER_MODEL_SIZE,
        device: str = "auto",
        compute_type: str = "default",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: object | None = None  # lazily constructed on first use, off the event loop

    def _load_model_blocking(self) -> object:
        # Blocking: downloads (first run) / mmaps the model and constructs the CTranslate2
        # runtime. Imported lazily so importing this module never requires the heavy optional
        # `voice` extra to be installed unless this provider is actually selected.
        import faster_whisper  # noqa: PLC0415

        return faster_whisper.WhisperModel(
            self._model_size, device=self._device, compute_type=self._compute_type
        )

    def _transcribe_blocking(
        self,
        model: object,
        audio_path: str,
        whisper_language: str | None,
        hints: tuple[str, ...],
    ) -> tuple[list[_WhisperSegment], _WhisperTranscriptionInfo]:
        whisper_model = cast("_WhisperModel", model)
        # `initial_prompt` is faster-whisper's own vocabulary-biasing lever — feeding it the
        # same watchlist-symbol/lexicon hints `voice.service.build_hints` already assembles
        # nudges decoding toward those tokens without constraining the output.
        initial_prompt = ", ".join(hints) if hints else None
        segments, info = whisper_model.transcribe(
            audio_path,
            language=whisper_language,
            initial_prompt=initial_prompt,
        )
        return list(segments), info

    async def transcribe(
        self, audio: bytes, *, language: str = "zh-TW", hints: tuple[str, ...] = ()
    ) -> Transcript:
        whisper_language = _WHISPER_LANGUAGE_CODES.get(language, language)
        if self._model is None:
            self._model = await anyio.to_thread.run_sync(self._load_model_blocking)

        # faster-whisper's `transcribe()` accepts a path, file-like object, or ndarray; a temp
        # file is the simplest correct choice for raw encoded audio bytes of an unknown
        # container (m4a/webm-opus per the API's own docstring) — faster-whisper delegates
        # decoding to PyAV, which sniffs the container from the file itself, not the extension.
        with tempfile.TemporaryDirectory() as tmp_dir:
            audio_path = Path(tmp_dir) / "audio.bin"
            audio_path.write_bytes(audio)
            segments, info = await anyio.to_thread.run_sync(
                self._transcribe_blocking,
                self._model,
                str(audio_path),
                whisper_language,
                hints,
            )

        transcript_segments = tuple(
            TranscriptSegment(
                text=seg.text.strip(), confidence=_logprob_to_confidence(seg.avg_logprob)
            )
            for seg in segments
            if seg.text.strip()
        )
        text = " ".join(seg.text for seg in transcript_segments).strip()
        low_confidence = not text or any(
            seg.confidence < _LOW_CONFIDENCE_THRESHOLD for seg in transcript_segments
        )
        logger.info(
            "faster_whisper_transcribed",
            detected_language=info.language,
            language_probability=info.language_probability,
            segment_count=len(transcript_segments),
        )
        return Transcript(
            text=text,
            language=language,
            segments=transcript_segments,
            low_confidence=low_confidence,
        )


def _logprob_to_confidence(avg_logprob: float) -> float:
    """Map whisper's `avg_logprob` (a mean log-probability, `<= 0`) onto the `[0, 1]` confidence
    the `TranscriptSegment` schema expects — `exp(avg_logprob)` is the standard reasonable
    mapping (it recovers the actual per-token probability at `avg_logprob == 0`)."""
    return max(0.0, min(1.0, math.exp(avg_logprob)))


# Edge neural voice IDs, verified against `edge_tts.list_voices()`'s live catalog (see the
# provider docstring) — a small config-shaped lookup dict, same idiom as
# `llm.registry._OPENAI_COMPAT_BASE_URLS`. zh-TW uses HsiaoChen (the same voice Microsoft ships
# as the default zh-TW neural voice); en/en-US uses Ava (a current-generation US English
# neural voice). Extend this table, never branch on `language` ad hoc in `speak()`.
_EDGE_TTS_VOICES: dict[str, str] = {
    "zh-TW": "zh-TW-HsiaoChenNeural",
    "zh": "zh-TW-HsiaoChenNeural",
    "en-US": "en-US-AvaNeural",
    "en": "en-US-AvaNeural",
    "en-GB": "en-GB-LibbyNeural",
}
_DEFAULT_EDGE_TTS_VOICE = "zh-TW-HsiaoChenNeural"


class EdgeTTSSpeaker:
    """TTS via Microsoft Edge's neural voices (`edge-tts`). `edge-tts`'s `Communicate` API is
    itself `async def` (it streams over a websocket via `aiohttp`) — no threadpool wrapping
    needed here, unlike the blocking `faster-whisper` SDK above. Produces MP3 (`audio/mpeg`),
    matching `StubSpeaker`'s declared default content type."""

    def __init__(self, *, content_type: str = "audio/mpeg") -> None:
        self._content_type = content_type

    async def speak(self, ssml_or_text: str, *, language: str = "zh-TW") -> SpeechAudio:
        import edge_tts  # noqa: PLC0415

        voice = _EDGE_TTS_VOICES.get(language, _DEFAULT_EDGE_TTS_VOICE)
        communicate = edge_tts.Communicate(ssml_or_text, voice=voice)
        chunks = bytearray()
        async for message in communicate.stream():
            # `TTSChunk`'s `data` key is only present on `type == "audio"` messages (word/
            # sentence-boundary messages carry no audio) — the `"data" in message` check makes
            # that TypedDict-narrowing explicit rather than relying on the `type` check alone.
            if message["type"] == "audio" and "data" in message:
                chunks.extend(message["data"])
        logger.info("edge_tts_spoken", voice=voice, audio_bytes=len(chunks))
        return SpeechAudio(
            audio=bytes(chunks),
            content_type=self._content_type,
            spoken_text=ssml_or_text,
        )


class OpenAITTSSpeaker:
    """TTS via OpenAI's real Audio Speech API (`client.audio.speech.create(...)`, `AsyncOpenAI`).
    Verified against the installed `openai` SDK (2.52.0): `AsyncSpeech.create()` is a genuinely
    `async def` method (no threadpool wrapping needed, same as `openai_client.py`'s `AsyncOpenAI`
    usage) and returns an `HttpxBinaryResponseContent` whose `.aread()` gives the raw encoded
    audio bytes. `model` is `openai.types.audio.speech_model.SpeechModel`, a
    `Literal["tts-1", "tts-1-hd", "gpt-4o-mini-tts", "gpt-4o-mini-tts-2025-12-15"]`; `voice` is
    `openai.types.audio.speech_create_params.Voice`, currently
    `Literal["alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin",
    "cedar"]` (or a raw `str`/`VoiceID` for custom cloned voices — not used here). `tts-1-hd` is
    the default: higher-fidelity than `tts-1` at the same per-character price, and unlike
    `gpt-4o-mini-tts` its output is deterministic take-to-take, which matters more for a
    read-aloud caveat than the newer model's steerable-style features.

    Unlike `EdgeTTSSpeaker`, OpenAI's TTS voices are not per-language — there is no `voice` id
    named for zh-TW vs. en-US the way `edge-tts` has `HsiaoChenNeural`/`AvaNeural`. OpenAI
    documents these ~10 voices as multilingual readers that pronounce whatever text they're
    given (including Chinese) in the same voice identity, not as a per-locale voice catalog. So
    this provider does *not* branch on `language` to pick a different voice id — `voice` is a
    single constructor-level default (`"alloy"`, judged a neutral, clear multilingual read) that
    keeps `language` only for `Speaker` Protocol conformance, the same way `StubSpeaker` accepts
    and discards it. The API takes no separate locale/language field to normalize text by, so
    there is nothing more to verify or wire through here — this is the honest limit, not an
    oversight.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        voice: str = "alloy",
        model: str = "tts-1-hd",
        response_format: str = "mp3",
        content_type: str = "audio/mpeg",
    ) -> None:
        self._api_key = api_key
        self._voice = voice
        self._model = model
        self._response_format = response_format
        self._content_type = content_type

    async def speak(self, ssml_or_text: str, *, language: str = "zh-TW") -> SpeechAudio:
        del language  # accepted for Protocol conformance; OpenAI TTS voices aren't per-locale
        from openai import AsyncOpenAI  # noqa: PLC0415

        client = AsyncOpenAI(api_key=self._api_key)
        response = await client.audio.speech.create(
            model=self._model,  # type: ignore[arg-type] — validated string, SDK's own Literal
            voice=self._voice,  # type: ignore[arg-type] — validated string, SDK's own Literal
            input=ssml_or_text,
            response_format=self._response_format,  # type: ignore[arg-type]
        )
        audio_bytes = await response.aread()
        logger.info("openai_tts_spoken", voice=self._voice, audio_bytes=len(audio_bytes))
        return SpeechAudio(
            audio=audio_bytes,
            content_type=self._content_type,
            spoken_text=ssml_or_text,
        )


__all__ = [
    "EdgeTTSSpeaker",
    "FasterWhisperTranscriber",
    "OpenAITTSSpeaker",
    "Speaker",
    "StubSpeaker",
    "StubTranscriber",
    "Transcriber",
]
