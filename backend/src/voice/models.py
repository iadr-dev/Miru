"""Voice wire shapes. Edge-only STT/TTS — a transcript is returned for the
user to confirm/edit; it does NOT create a turn (the confirm-before-send safety step).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    confidence: float = Field(ge=0.0, le=1.0)


class Transcript(BaseModel):
    """STT output: text + per-segment confidence. The client shows this for edit; the user
    submits the (possibly corrected) text via the normal `POST /agent/query`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str
    language: str
    segments: tuple[TranscriptSegment, ...] = Field(default_factory=tuple)
    low_confidence: bool = False


class SpeechAudio(BaseModel):
    """TTS output: encoded audio bytes + its format. The spoken text always includes the
    caveat (enforced in `voice.service.speak`)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audio: bytes
    content_type: str
    spoken_text: str


__all__ = ["SpeechAudio", "Transcript", "TranscriptSegment"]
