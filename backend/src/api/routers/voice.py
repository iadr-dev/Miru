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

import structlog
from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from accounts.entitlements import VOICE_DAILY_LIMIT, VOICE_REQUESTS_PER_MINUTE
from accounts.models import User
from api.dependencies import AppState, get_session, get_settings_dep, get_state, require_user
from api.errors import ApiError
from config.settings import Settings
from memory.watchlists import WatchlistStore
from usage.store import VoiceKind
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

_VOICE_WINDOW_SECONDS = 60


def _is_paid_provider(settings: Settings, kind: VoiceKind) -> bool:
    """Only a provider that bills per call is capped per day. Local speech-to-text and the free
    Edge voices cost nothing per request, so they get no quota - voice is not a paid feature."""
    return kind == "tts" and settings.voice_tts_provider == "openai"


async def _claim_voice(state: AppState, user: User, kind: VoiceKind, *, paid: bool) -> bool:
    """Bound voice before any provider work. Always a per-minute rate (abuse / CPU protection, the
    same for every tier). A per-UTC-day cap applies only when `paid` - a provider that bills per
    call. Returns whether a daily claim was taken, so a failed request can give it back."""
    verdict = await state.rate_limiter.check(
        f"voice:{user.id}",
        max_requests=VOICE_REQUESTS_PER_MINUTE,
        window_seconds=_VOICE_WINDOW_SECONDS,
    )
    if not verdict.allowed:
        raise ApiError(
            status_code=429,
            code="rate_limited",
            message="Too many voice requests. Please slow down and retry shortly.",
            retryable=True,
        )
    if not paid:
        return False
    claim = await state.usage_store.reserve_daily_voice(
        user_id=user.id, kind=kind, limit=VOICE_DAILY_LIMIT
    )
    if not claim.allowed:
        raise ApiError(
            status_code=429,
            code="voice_limit_reached",
            message=(
                f"Daily voice limit reached ({VOICE_DAILY_LIMIT} requests/day). Try again tomorrow."
            ),
            retryable=False,
        )
    return True


logger = structlog.get_logger(__name__)

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
    state: AppState = Depends(get_state),
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

    claimed = await _claim_voice(state, user, "stt", paid=_is_paid_provider(settings, "stt"))
    watchlist_terms = await _watchlist_terms(session, user_id=user.id)
    try:
        return await transcribe_service(
            audio,
            _transcriber(settings),
            language=body.language,
            watchlist_terms=watchlist_terms,
            symbol_universe=frozenset(watchlist_terms),
        )
    except Exception:
        if claimed:
            await state.usage_store.refund_daily_voice(user_id=user.id, kind="stt")
        raise


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
    state: AppState = Depends(get_state),
) -> SpeakResponse:
    """TTS read-aloud (Free, fair-use bounded like STT: a per-minute rate and a daily cap). The
    spoken text is caveat-guaranteed before synthesis."""
    claimed = await _claim_voice(state, user, "tts", paid=_is_paid_provider(settings, "tts"))
    try:
        audio: SpeechAudio = await speak_service(
            body.text, _speaker(settings), language=body.language
        )
    except Exception:
        if claimed:
            await state.usage_store.refund_daily_voice(user_id=user.id, kind="tts")
        raise
    if claimed:
        # A provider that bills per call: leave a cost trail (characters synthesized) for ops.
        logger.info("voice_paid_tts", user_id=user.id, chars=len(audio.spoken_text))
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
