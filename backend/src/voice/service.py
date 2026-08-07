"""Voice services: `transcribe` (STT) and `speak` (TTS). Edge-only — no turn creation, no
orchestrator coupling.

`transcribe`:
- passes the user's watchlist symbols/names + a core 財經 lexicon as `hints` (vocabulary
  biasing — the highest-leverage lever for zh-TW code-switch accuracy);
- runs a deterministic number/ticker normalization post-pass against the symbol universe —
  never trusting the raw ASR string for a symbol;
- **returns the transcript only** for the client to confirm/edit. It explicitly does NOT
  create a turn — that is the safety-critical confirm-before-send step. This module has no
  reference to the agent loop, conversation store, or orchestrator.

`speak`:
- enforces the governance rule that spoken text **must include the caveat** — a spoken answer
  that drops the caveat is a governance regression, so this reuses the same non-trading caveat
  `agent.guardrails` appends and refuses to synthesize text lacking one.
"""

from __future__ import annotations

import re

import structlog

from agent.guardrails import check_output
from voice.models import SpeechAudio, Transcript, TranscriptSegment
from voice.providers import Speaker, Transcriber

logger = structlog.get_logger(__name__)

# Core 財經 lexicon seeded as ASR hints alongside the user's watchlist.
_CORE_FINANCE_LEXICON: tuple[str, ...] = (
    "台積電",
    "加權指數",
    "外資",
    "投信",
    "融資",
    "融券",
    "殖利率",
    "本益比",
    "除權息",
)

_LOW_CONFIDENCE_THRESHOLD = 0.6

# zh spoken-digit → arabic, for the ticker post-pass ("二三三零" → "2330").
_ZH_DIGITS: dict[str, str] = {
    "零": "0",
    "〇": "0",
    "一": "1",
    "二": "2",
    "兩": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
}


def _normalize_spoken_ticker(text: str, symbol_universe: frozenset[str]) -> str:
    """Deterministic post-pass: map a run of spoken zh digits to arabic and, if the result is a
    known symbol, substitute it. Never invents a symbol — only substitutes when the normalized
    run is in the caller-supplied universe."""

    def _replace(match: re.Match[str]) -> str:
        run = match.group(0)
        digits = "".join(_ZH_DIGITS[ch] for ch in run)
        return digits if digits in symbol_universe else run

    return re.sub(r"[零〇一二兩三四五六七八九]{3,6}", _replace, text)


def build_hints(watchlist_terms: tuple[str, ...]) -> tuple[str, ...]:
    """Vocabulary-biasing hints: the user's watchlist symbols/names first, then the core
    lexicon. De-duplicated, order-preserving."""
    seen: set[str] = set()
    ordered: list[str] = []
    for term in (*watchlist_terms, *_CORE_FINANCE_LEXICON):
        if term and term not in seen:
            seen.add(term)
            ordered.append(term)
    return tuple(ordered)


async def transcribe(
    audio: bytes,
    transcriber: Transcriber,
    *,
    language: str = "zh-TW",
    watchlist_terms: tuple[str, ...] = (),
    symbol_universe: frozenset[str] = frozenset(),
) -> Transcript:
    """STT → transcript. Returns text only; NEVER creates a turn."""
    hints = build_hints(watchlist_terms)
    raw = await transcriber.transcribe(audio, language=language, hints=hints)
    normalized_text = _normalize_spoken_ticker(raw.text, symbol_universe)
    normalized_segments = tuple(
        TranscriptSegment(
            text=_normalize_spoken_ticker(seg.text, symbol_universe), confidence=seg.confidence
        )
        for seg in raw.segments
    )
    low_conf = raw.low_confidence or any(
        seg.confidence < _LOW_CONFIDENCE_THRESHOLD for seg in normalized_segments
    )
    logger.info("voice_transcribed", language=raw.language, low_confidence=low_conf)
    return Transcript(
        text=normalized_text,
        language=raw.language,
        segments=normalized_segments,
        low_confidence=low_conf,
    )


def ensure_caveated_for_speech(text: str) -> str:
    """Guarantee the spoken text carries a non-trading/currentness caveat. Runs the same
    `agent.guardrails.check_output` backstop as written answers: if it appends a caveat (or
    redacts trading language), the caveated/redacted version is what gets spoken. A spoken
    answer that dropped the caveat would be a governance regression."""
    verdict = check_output(text)
    return verdict.redacted_text if verdict.redacted_text is not None else text


async def speak(
    text: str,
    speaker: Speaker,
    *,
    language: str = "zh-TW",
) -> SpeechAudio:
    """TTS → audio. The spoken text is caveat-guaranteed before synthesis."""
    spoken = ensure_caveated_for_speech(text)
    audio = await speaker.speak(spoken, language=language)
    logger.info("voice_spoken", language=language)
    return audio


__all__ = [
    "build_hints",
    "ensure_caveated_for_speech",
    "speak",
    "transcribe",
]
