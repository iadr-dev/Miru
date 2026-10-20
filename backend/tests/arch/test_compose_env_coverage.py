"""Docker Compose env-var coverage gate.

`docker-compose.yml` deliberately forwards config vars **explicitly** (not `env_file: .env`) —
`.env` also carries host-only values (a bare-metal `DATABASE_URL=sqlite+...`, `INGEST_ON_STARTUP=
true`) that would silently override the compose stack's own Postgres/Redis-hostname wiring and
its no-auto-ingest-in-containers invariant if loaded wholesale (see the docstring at the top of
docker-compose.yml). The cost of that choice is that a new `Settings` field is invisible to a
container unless someone remembers to add a matching line — which degrades *quietly* (a 503, a
stub falling back to its default, never a startup error) rather than loudly. This test is the
loud version: it fails CI the moment `config/settings.py` grows a field that isn't either
forwarded in the compose anchor or explicitly allow-listed as compose-internal.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from config.settings import Settings

BACKEND_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = BACKEND_ROOT / "docker-compose.yml"

# Deliberately NOT forwarded from .env — these are pinned inside docker-compose.yml's x-app-env
# to compose-internal values (service hostnames, dev-parity backends) that a host .env must never
# override. Extending this set is a real architecture decision, not a convenience — if a field
# belongs here, say why in the same style as the existing entries.
COMPOSE_INTERNAL_FIELDS = frozenset(
    {
        "database_url",  # pinned to the compose postgres service, never the host's sqlite dev URL
        "redis_url",  # pinned to the compose redis service
        "event_bus_backend",  # forced "redis" in compose — the whole point of the dev-parity stack
        "rate_limiter_backend",  # forced "redis" in compose, same reason
        "parquet_root",  # pinned to the shared named volume path, not a host path
        "env",  # forced "development" in compose regardless of the host .env
        "ingest_on_startup",  # forced "false" in compose (governance: no startup network calls)
    }
)


def _settings_field_names() -> set[str]:
    """Every `Settings` field, via Pydantic's own model introspection — not a regex over the
    source, so it can't drift from whatever pydantic actually considers a field."""
    return set(Settings.model_fields.keys())


def _compose_forwarded_env_names() -> set[str]:
    """The env var names set inside `x-app-env`'s anchor block, lowercased to match `Settings`
    field names (pydantic-settings' env-var mapping is case-insensitive)."""
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    # PyYAML chokes on the bare `&app-env`/`<<: *app-env` merge-key idiom only if a custom
    # constructor isn't registered for it in some environments; compose files are plain YAML 1.1
    # anchors/aliases, which PyYAML's default loader already understands natively — no
    # special-casing needed here.
    doc = yaml.safe_load(raw)
    app_env = doc["x-app-env"]
    assert isinstance(app_env, dict), "x-app-env must be a flat mapping of ENV_NAME: value"
    return {key.lower() for key in app_env}


def test_every_settings_field_is_forwarded_or_allow_listed() -> None:
    settings_fields = _settings_field_names()
    forwarded = _compose_forwarded_env_names()

    missing = settings_fields - forwarded - COMPOSE_INTERNAL_FIELDS
    assert not missing, (
        "config/settings.py field(s) not reachable inside the compose stack — add a matching "
        f"line to docker-compose.yml's x-app-env, or to COMPOSE_INTERNAL_FIELDS above if this is "
        f"deliberately compose-internal: {sorted(missing)}"
    )


def test_no_stale_allow_list_entries() -> None:
    """Catches the opposite drift: a field renamed/removed from Settings but left in the
    allow-list, which would otherwise silently stop this test from covering anything real."""
    settings_fields = _settings_field_names()
    stale = COMPOSE_INTERNAL_FIELDS - settings_fields
    assert not stale, (
        f"COMPOSE_INTERNAL_FIELDS references field(s) no longer in Settings: {sorted(stale)}"
    )


def test_compose_anchor_values_are_pydantic_settings_env_syntax() -> None:
    """Every forwarded value must be `${VAR:-default}` or `${VAR}` interpolation, or a literal —
    never a bare unset-looking placeholder that would fail Settings' own type coercion (the
    concrete bug this test exists to prevent: an int-typed field forwarded as `${VAR:-}`, which
    Settings then fails to parse as an empty string)."""
    raw = COMPOSE_PATH.read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    app_env = doc["x-app-env"]

    int_or_float_fields = {
        name
        for name, field in Settings.model_fields.items()
        if field.annotation in (int, float) or str(field.annotation) in ("int", "float")
    }

    bad: list[str] = []
    for key, value in app_env.items():
        field_name = key.lower()
        if field_name not in int_or_float_fields:
            continue
        text = str(value)
        # `${VAR:-}` (empty-string default) on a numeric field is the exact failure mode this
        # test guards against — an explicit numeric default (or a bare passthrough with no
        # default, which pydantic-settings will then require the host to supply) is fine.
        if re.search(r"\$\{[A-Z_]+:-\}$", text):
            bad.append(f"{key} forwards an empty-string default onto numeric field {field_name!r}")
    assert not bad, "\n".join(bad)
