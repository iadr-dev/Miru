"""Write-path rules for long-term memory content, in `schemas/` (the lowest layer) so the memory
store and the agent's `propose_memory` tool apply the exact same checks: no credentials, no raw
source payloads, no trading recommendations, and no tone/format instructions in a `preference`.
Pure functions over plain data - no I/O.
"""

from __future__ import annotations

import re
from typing import Final

# Keyword/pattern heuristics — a deliberate first pass (documented precedent:
# `agent/guardrails.py` uses the same style of keyword/pattern matching for the identical
# reason: cheap, auditable, and good enough to catch the obvious cases without an LLM call on
# every memory write).
_CREDENTIAL_KEY_MARKERS: Final[tuple[str, ...]] = (
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "secret",
    "password",
    "ciphertext",
    "private_key",
)
_RAW_PAYLOAD_KEY_MARKERS: Final[tuple[str, ...]] = ("raw_payload", "raw_response", "source_payload")
# Free text can carry a secret under any key name, so string values are scanned for the shapes
# provider keys and bearer tokens take, and for someone writing out a password.
_CREDENTIAL_VALUE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bsk-[a-z0-9_-]{8,}",
        r"\bAIza[0-9a-z_-]{20,}",
        r"\bbearer\s+[a-z0-9._~+/-]{16,}",
        r"(api[ _-]?key|password|passwd|secret|密碼|金鑰)\s*(是|為|is|[:=：])",
    )
)
_TRADING_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\byou should (buy|sell|hold)\b",
        r"\b(buy|sell|hold)\s+(rating|signal|recommendation|call)\b",
        r"\bprice target\b",
        r"\btarget price\b",
        r"\bposition siz(e|ing)\b",
        r"目標價",
        r"買進建議",
        r"賣出建議",
    )
)


# Voice, tone and answer format are the curated 語氣 presets' job (modules/23): a free-text style
# instruction smuggled into memory would be an uncurated second persona, and is what once made every
# answer end with a "TL;DR". A `preference` memory records analysis focus, not how Miru talks.
_STYLE_DIRECTIVE_PATTERNS: Final[tuple[re.Pattern[str], ...]] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"tl;?\s*dr",
        r"\bbullet",
        r"\btone\b",
        r"\bverbos",
        r"\bconcise\b",
        r"語氣",
        r"口吻",
        r"說話(風格|方式)",
        r"條列",
        r"簡潔",
        r"精簡",
        r"一句話總結",
    )
)

STYLE_DIRECTIVE_MESSAGE: Final = (
    "tone and answer-format instructions are managed in Tone settings (語氣設定), not memory"
)


def scan_preference_for_style_directive(content: dict[str, object]) -> str | None:
    """A reason when a `preference` memory is really a tone/format instruction, else `None`."""
    for value in content.values():
        if isinstance(value, str) and any(p.search(value) for p in _STYLE_DIRECTIVE_PATTERNS):
            return STYLE_DIRECTIVE_MESSAGE
    return None


def scan_content_for_governance_violations(content: dict[str, object]) -> str | None:
    """Return a human-readable reason if `content` violates a governance rule, else `None`.
    Checks key names (credentials/raw payloads are conventionally under a recognizable key)
    and string values (trading language can appear in free text regardless of key name)."""
    for key, value in content.items():
        lowered_key = key.lower()
        if any(marker in lowered_key for marker in _CREDENTIAL_KEY_MARKERS):
            return f"content key {key!r} looks like a credential — never stored in memory"
        if any(marker in lowered_key for marker in _RAW_PAYLOAD_KEY_MARKERS):
            return f"content key {key!r} looks like a raw source payload — never stored verbatim"
        if isinstance(value, str) and any(
            pattern.search(value) for pattern in _CREDENTIAL_VALUE_PATTERNS
        ):
            return f"content key {key!r} looks like it contains a credential - never stored"
        if isinstance(value, str) and any(pattern.search(value) for pattern in _TRADING_PATTERNS):
            return f"content key {key!r} contains trading-recommendation language"
    return None


__all__ = [
    "STYLE_DIRECTIVE_MESSAGE",
    "scan_content_for_governance_violations",
    "scan_preference_for_style_directive",
]
