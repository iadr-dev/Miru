"""Voice — edge-only STT/TTS.

`/voice/transcribe` returns a transcript for the user to confirm/edit and explicitly does NOT
create a turn; `/voice/speak` synthesizes caveat-guaranteed audio. Providers are config-selected
(self-host whisper or cloud) behind the `Transcriber`/`Speaker` Protocols — no hardcoded
provider/model. No orchestrator/agent-loop coupling.
"""

from __future__ import annotations

from voice.models import SpeechAudio, Transcript, TranscriptSegment
from voice.providers import Speaker, StubSpeaker, StubTranscriber, Transcriber
from voice.service import ensure_caveated_for_speech, speak, transcribe

__all__ = [
    "Speaker",
    "SpeechAudio",
    "StubSpeaker",
    "StubTranscriber",
    "Transcriber",
    "Transcript",
    "TranscriptSegment",
    "ensure_caveated_for_speech",
    "speak",
    "transcribe",
]
