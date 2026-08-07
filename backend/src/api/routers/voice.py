"""Voice edge endpoints: `POST /voice/transcribe` (STT) and `POST /voice/speak` (TTS).

Thin wiring over the already-built `voice.service`. Transcription is Free (quota-bounded, the
beginner ask-loop) — every authenticated caller may use it. Read-aloud speech is a Premium+
feature, gated the same way `scheduler.py` gates its Pro feature: an unentitled caller is refused
here (the `tts_read_aloud` feature flag), never billed a masked-teaser shape (that shape is for
`sponsor` *data*, not a whole binary-audio endpoint).

The concrete STT/TTS providers are config-selected; only the deterministic stubs run in the
default profile, so this router performs no live provider call there. Raw audio is never
persisted — it lives only for the duration of the request.
"""

from __future__ import annotations

import base64

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.entitlements import feature_allowed
from accounts.models import User
from api.dependencies import get_session, get_settings_dep, require_user
from api.errors import ApiError
from config.settings import Settings
from memory.watchlists import WatchlistStore
from voice.models import SpeechAudio, Transcript
from voice.providers import Speaker, StubSpeaker, StubTranscriber, Transcriber
from voice.service import speak as speak_service
from voice.service import transcribe as transcribe_service

router = APIRouter(tags=["voice"])

_TTS_FEATURE = "tts_read_aloud"
_STUB_PROVIDER = "stub"
_MAX_AUDIO_BYTES = 12_000_000  # ~a minute of compressed audio; a hard upper bound, not a quota


def _transcriber(settings: Settings) -> Transcriber:
    """Config-selected STT provider. Only the stub ships in the default profile; a real cloud/
    self-host provider is wired behind the same Protocol once configured (it would live under
    `sources/` transport-wise)."""
    if settings.voice_stt_provider == _STUB_PROVIDER:
        return StubTranscriber()
    raise ApiError(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        code="voice_provider_unconfigured",
        message=f"STT provider {settings.voice_stt_provider!r} is not wired.",
        retryable=False,
    )


def _speaker(settings: Settings) -> Speaker:
    if settings.voice_tts_provider == _STUB_PROVIDER:
        return StubSpeaker()
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
    """TTS read-aloud (Premium+). The spoken text is caveat-guaranteed before synthesis."""
    if not feature_allowed(user, _TTS_FEATURE):
        raise ApiError(
            status_code=status.HTTP_403_FORBIDDEN,
            code="feature_not_entitled",
            message="Read-aloud requires a Premium or Pro subscription.",
            retryable=False,
        )
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
