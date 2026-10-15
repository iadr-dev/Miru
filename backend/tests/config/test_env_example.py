"""`.env.example` is the template people copy; a key the code does not read is a setting that
silently does nothing, and a default that drifted from the code misleads whoever keeps it."""

from __future__ import annotations

import re
from pathlib import Path

from config.settings import Settings

_BACKEND = Path(__file__).resolve().parents[2]
# Read by the Docker Compose stack, not by the app.
_COMPOSE_ONLY = {"POSTGRES_PASSWORD"}


def _example_keys() -> dict[str, str]:
    text = (_BACKEND / ".env.example").read_text(encoding="utf-8")
    return {
        key: value.strip()
        for key, value in re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=([^#\n]*)", text, re.M)
    }


def test_every_key_in_the_example_is_a_setting_the_app_reads() -> None:
    fields = {name.upper() for name in Settings.model_fields}
    unknown = set(_example_keys()) - fields - _COMPOSE_ONLY
    assert not unknown, f".env.example has keys no setting reads: {sorted(unknown)}"


def test_numeric_defaults_in_the_example_match_the_code() -> None:
    fields = Settings.model_fields
    drifted: list[str] = []
    for key, raw in _example_keys().items():
        field = fields.get(key.lower())
        if (
            field is None
            or not isinstance(field.default, int | float)
            or isinstance(field.default, bool)
        ):
            continue
        if raw and float(raw) != float(field.default):
            drifted.append(f"{key}: example {raw}, code {field.default}")
    assert not drifted, drifted
