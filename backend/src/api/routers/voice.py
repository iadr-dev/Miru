"""Voice edge endpoints: `POST /voice/transcribe` (STT) and `POST /voice/speak` (TTS).

Thin wiring over the already-built `voice.service`. Both STT and TTS are Free, quota-bounded
(not tier-gated) — voice conversation mode (the composer's continuous hands-free overlay) is
built on both and is itself a Free-tier feature end to end.

The concrete STT/TTS providers are config-selected; only the deterministic stubs run in the
default profile, so this router performs no live provider call there. Raw audio is never
persisted — it lives only for the duration of the request.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.models import User
from api.dependencies import get_session, get_settings_dep, require_user
from api.errors import ApiError
from config.settings import Settings
from memory.watchlists import WatchlistStore
from voice.models import SpeechAudio, Transcript
from voice.providers import (
    EdgeTTSSpeaker,
    FasterWhisperTranscriber,
    OpenAITTSSpeaker,
    Speaker,
    StubSpeaker,
    StubTranscriber,
    Transcriber,
)
from voice.service import speak as speak_service
from voice.service import transcribe as transcribe_service

router = APIRouter(tags=["voice"])

_STUB_PROVIDER = "stub"
_MAX_AUDIO_BYTES = 12_000_000  # ~a minute of compressed audio; a hard upper bound, not a quota

# A `FasterWhisperTranscriber` owns a loaded model — constructed once per provider id, not per
# request, so repeated calls don't re-load the model from disk/hub each time. Config-selected,
# same idiom as `llm.registry`'s per-role client construction; never hardcoded elsewhere.
_faster_whisper_transcriber: FasterWhisperTranscriber | None = None


def _transcriber(settings: Settings) -> Transcriber:
    """Config-selected STT provider. Only the stub ships in the default profile; real providers
    (`faster_whisper`) are opt-in via `voice_stt_provider`."""
    global _faster_whisper_transcriber  # noqa: PLW0603 — single lazily-built module-level cache
    if settings.voice_stt_provider == _STUB_PROVIDER:
        return StubTranscriber()
    if settings.voice_stt_provider == "faster_whisper":
        if _faster_whisper_transcriber is None:
            _faster_whisper_transcriber = FasterWhisperTranscriber()
        return _faster_whisper_transcriber
    raise ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="voice_provider_unconfigured",
        message=f"STT provider {settings.voice_stt_provider!r} is not wired.",
        retryable=False,
    )


def _speaker(settings: Settings) -> Speaker:
    if settings.voice_tts_provider == _STUB_PROVIDER:
        return StubSpeaker()
    if settings.voice_tts_provider == "edge_tts":
        return EdgeTTSSpeaker()
    if settings.voice_tts_provider == "openai":
        return OpenAITTSSpeaker(api_key=settings.openai_api_key)
    raise ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="voice_provider_unconfigured",
        message=f"TTS provider {settings.voice_tts_provider!r} is not wired.",
        retryable=False,
    )


class TranscribeRequest(BaseModel):
    audio_base64: str = Field(min_length=1, description="Base64-encoded audio (m4a/webm-opus).")
    language: str = "zh-TW"


@router.post("/voice/transcribe")
async def transcribe(
    body: TranscribeRequest,
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings_dep),
    session: AsyncSession = Depends(get_session),
) -> Transcript:
    """STT → transcript + confidence. Returns text only; never creates a turn (the client
    confirms/edits, then calls `POST /agent/query`)."""
    try:
        audio = base64.b64decode(body.audio_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ApiError(
            status_code=422,
            code="invalid_request",
            message="audio_base64 is not valid base64.",
            retryable=False,
        ) from exc
    if len(audio) > _MAX_AUDIO_BYTES:
        raise ApiError(
            status_code=413,
            code="audio_too_large",
            message="Audio payload exceeds the maximum size.",
            retryable=False,
        )

    watchlist_terms = await _watchlist_terms(session, user_id=user.id)
    return await transcribe_service(
        audio,
        _transcriber(settings),
        language=body.language,
        watchlist_terms=watchlist_terms,
        symbol_universe=frozenset(watchlist_terms),
    )


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)
    language: str = "zh-TW"


class SpeakResponse(BaseModel):
    """The synthesized audio, base64-encoded for a JSON envelope. `spoken_text` is the exact
    caveated text that was voiced — the caveat is guaranteed present by `voice.service.speak`."""

    audio_base64: str
    content_type: str
    spoken_text: str


@router.post("/voice/speak")
async def speak(
    body: SpeakRequest,
    user: User = Depends(require_user),
    settings: Settings = Depends(get_settings_dep),
) -> SpeakResponse:
    """TTS read-aloud (Free, quota-bounded like STT). The spoken text is caveat-guaranteed
    before synthesis."""
    del user  # accepted for the auth boundary only — TTS is unrestricted by tier
    audio: SpeechAudio = await speak_service(body.text, _speaker(settings), language=body.language)
    return SpeakResponse(
        audio_base64=base64.b64encode(audio.audio).decode("ascii"),
        content_type=audio.content_type,
        spoken_text=audio.spoken_text,
    )


async def _watchlist_terms(session: AsyncSession, *, user_id: str) -> tuple[str, ...]:
    """The caller's watchlist symbols, fed to STT as vocabulary-biasing hints + the symbol
    universe the deterministic ticker post-pass matches against."""
    snapshot = await WatchlistStore(session).snapshot(user_id)
    terms: list[str] = []
    for group in snapshot:
        terms.extend(group.symbols)
    return tuple(terms)


__all__ = ["router"]
