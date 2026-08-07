"""Personality-preset discovery + validation + selection.

Mirrors the skill registry: presets are file-defined YAML in `presets/`, validated on load
against `personality.models.PersonalityPreset`, and indexed by id. Selection resolves, in fixed
precedence, `turn override → thread override → user setting → steady`.

The resolved fragment is only ever placed in `PromptLayers.personality` — the [2] layer,
appended *below* the immovable governance prompt. Because `agent.prompts.assemble_system_prompt`
concatenates governance first and the final answer is always scanned by
`agent.guardrails.check_output`, a preset can style tone but can never weaken a caveat or emit a
signal. This is proven by a test, not just asserted here.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import structlog
import yaml
from pydantic import ValidationError

from personality.models import PersonalityPreset

logger = structlog.get_logger(__name__)

_DEFAULT_PRESET_DIR = Path(__file__).parent / "presets"
_FALLBACK_ID = "steady"


class PersonalityValidationError(ValueError):
    """A preset file is malformed or fails schema validation — the registry refuses to load a
    bad preset rather than silently skipping it (a governance-reviewed fragment must never be
    silently dropped)."""


class UnknownPersonalityError(LookupError):
    pass


def parse_preset_file(path: Path) -> PersonalityPreset:
    raw_text = path.read_text(encoding="utf-8")
    loaded: object
    try:
        loaded = yaml.safe_load(raw_text) or {}
    except yaml.YAMLError as exc:
        raise PersonalityValidationError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise PersonalityValidationError(f"{path}: preset must be a YAML mapping")
    try:
        return PersonalityPreset.model_validate(cast("dict[str, object]", loaded))
    except ValidationError as exc:
        raise PersonalityValidationError(f"{path}: {exc}") from exc


class PersonalityRegistry:
    def __init__(self) -> None:
        self._presets: dict[str, PersonalityPreset] = {}
        self._default_id: str = _FALLBACK_ID

    def load(self, directories: Iterable[Path] | None = None) -> None:
        directories = list(directories) if directories is not None else [_DEFAULT_PRESET_DIR]
        for directory in directories:
            if not directory.is_dir():
                continue
            for preset_file in sorted(directory.glob("*.yaml")):
                preset = parse_preset_file(preset_file)
                if preset.id in self._presets:
                    raise PersonalityValidationError(
                        f"{preset_file}: duplicate preset id {preset.id!r}"
                    )
                self._presets[preset.id] = preset
                if preset.default:
                    self._default_id = preset.id
                logger.info("personality_preset_loaded", id=preset.id, version=preset.version)

    def get(self, preset_id: str) -> PersonalityPreset:
        preset = self._presets.get(preset_id)
        if preset is None:
            raise UnknownPersonalityError(preset_id)
        return preset

    def all_presets(self) -> list[PersonalityPreset]:
        return sorted(self._presets.values(), key=lambda p: p.id)

    def known_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._presets))

    @property
    def default_id(self) -> str:
        return self._default_id

    def resolve(
        self,
        *,
        turn_override: str | None = None,
        thread_override: str | None = None,
        user_preference: str | None = None,
    ) -> PersonalityPreset:
        """Precedence: turn override → thread override → user setting → default.
        An id that isn't loaded falls through to the next level (never a hard error — a stale
        preference must not break a turn), ultimately to the default preset."""
        for candidate in (turn_override, thread_override, user_preference, self._default_id):
            if candidate and candidate in self._presets:
                return self._presets[candidate]
        # Default id is guaranteed loaded if any preset with default:true was loaded; if the
        # registry is empty this raises, which is correct — a runtime with no presets is a
        # misconfiguration.
        return self.get(self._default_id)

    def resolve_fragment(
        self,
        *,
        turn_override: str | None = None,
        thread_override: str | None = None,
        user_preference: str | None = None,
    ) -> str:
        """The `PromptLayers.personality` string for a turn."""
        return self.resolve(
            turn_override=turn_override,
            thread_override=thread_override,
            user_preference=user_preference,
        ).fragment.strip()


def default_registry() -> PersonalityRegistry:
    """A registry pre-loaded from the built-in `presets/` directory."""
    registry = PersonalityRegistry()
    registry.load()
    return registry


__all__ = [
    "PersonalityRegistry",
    "PersonalityValidationError",
    "UnknownPersonalityError",
    "default_registry",
    "parse_preset_file",
]
